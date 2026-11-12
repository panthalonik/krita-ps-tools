/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "ps_filters.h"

#include <QByteArray>
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QHash>
#include <QMutex>
#include <QMutexLocker>
#include <QSharedPointer>
#include <QStringList>
#include <QVector>

#include <kpluginfactory.h>

#include <KoColorModelStandardIds.h>
#include <KoColorSpace.h>
#include <KoColorTransformation.h>

#include <filter/kis_filter_category_ids.h>
#include <filter/kis_filter_configuration.h>
#include <filter/kis_filter_registry.h>

#include "ps_kernels.h"
#include "ps_cameraraw.h"
#include "ps_axis_lock.h"

#include <KoColorProfile.h>
#include <KoUpdater.h>
#include <kis_default_bounds_base.h>
#include <kis_paint_device.h>

K_PLUGIN_FACTORY_WITH_JSON(KritaPsFiltersFactory, "kritapsfilters.json", registerPlugin<KritaPsFilters>();)

/// Read by the ps_tools Python plugin: QApplication.instance().property(
/// "ps_tools_engine"). This plugin is only built together with the engine
/// patches (krita/patches), so the value also says which of them are in:
///   1 = C++ filters + native masks on pass-through groups
///   2 = + Blend If in the compositing engine (patches/0002) and filter
///       layers that redraw by themselves after a scripted setFilter() (0003)
///   3 = + Photoshop clipping masks in the engine: "inherit alpha" clips to
///       the base below, the stack is composited on its own (0004), and PSD
///       files keep clipping and Blend If (0005)
///   4 = + Shift + drag axis lock of the brush in C++ (ps_axis_lock.cpp)
///   5 = + clipping to Pass Through groups: a pass-through group that is a
///       clipping base or is clipped is drawn isolated, like in Photoshop
///       (0009); new groups are Pass Through (0008)
///   6 = + FilterMask.setFilter() from scripts redraws the image (0011), the
///       Camera Raw Filter (pscameraraw)
static const int PS_TOOLS_ENGINE_LEVEL = 6;

KritaPsFilters::KritaPsFilters(QObject *parent, const QVariantList &)
    : QObject(parent)
{
    if (QCoreApplication::instance()) {
        QCoreApplication::instance()->setProperty("ps_tools_engine", PS_TOOLS_ENGINE_LEVEL);
        // Shift + drag axis lock: one cheap C++ filter instead of a Python one
        // that would see every event of the application (see ps_axis_lock.h)
        static PsAxisLock *axisLock = nullptr;
        if (!axisLock) {
            axisLock = new PsAxisLock(QCoreApplication::instance());
            QCoreApplication::instance()->installEventFilter(axisLock);
        }
    }
    KisFilterRegistry::instance()->add(KisFilterSP(new KisFilterPsColorLookup()));
    KisFilterRegistry::instance()->add(KisFilterSP(new KisFilterPsSelectiveColor()));
    KisFilterRegistry::instance()->add(KisFilterSP(new KisFilterPsBlendIf()));
    KisFilterRegistry::instance()->add(KisFilterSP(new KisFilterPsCameraRaw()));
}

KritaPsFilters::~KritaPsFilters()
{
}

namespace {

enum Layout {
    BgrU8,          // Krita's integer RGB spaces are stored B, G, R, A
    BgrU16,
    RgbF16,         // the float ones R, G, B, A
    RgbF32,
    Other           // anything else goes through 16-bit RGBA
};

Layout layoutOf(const KoColorSpace *cs)
{
    if (cs->colorModelId() != RGBAColorModelID) return Other;
    const KoID depth = cs->colorDepthId();
    if (depth == Integer8BitsColorDepthID && cs->pixelSize() == 4) return BgrU8;
    if (depth == Integer16BitsColorDepthID && cs->pixelSize() == 8) return BgrU16;
    if (depth == Float16BitsColorDepthID && cs->pixelSize() == 8) return RgbF16;
    if (depth == Float32BitsColorDepthID && cs->pixelSize() == 16) return RgbF32;
    return Other;
}

/// Changes the colour of the pixels, never their alpha.
/// Op: void operator()(float rgb[3]) const - normalised values, in place.
template<typename Op>
class PsColorTransformation : public KoColorTransformation
{
public:
    PsColorTransformation(const KoColorSpace *cs, const Op &op)
        : m_cs(cs), m_layout(layoutOf(cs)), m_op(op)
    {
    }

    void transform(const quint8 *src, quint8 *dst, qint32 nPixels) const override
    {
        if (nPixels <= 0) return;
        switch (m_layout) {
        case BgrU8:
            ps::runColor<quint8>(src, dst, nPixels, 2, 1, 0, 3, m_op);
            return;
        case BgrU16:
            ps::runColor<quint16>(src, dst, nPixels, 2, 1, 0, 3, m_op);
            return;
        case RgbF16:
            ps::runColor<ps::Half>(src, dst, nPixels, 0, 1, 2, 3, m_op);
            return;
        case RgbF32:
            ps::runColor<float>(src, dst, nPixels, 0, 1, 2, 3, m_op);
            return;
        case Other:
            break;
        }
        // CMYK, Lab, grayscale...: adjust a 16-bit RGB version of the pixels
        m_buffer.resize(nPixels * 8);
        m_cs->toRgbA16(src, m_buffer.data(), nPixels);
        ps::runColor<quint16>(m_buffer.constData(), m_buffer.data(), nPixels, 2, 1, 0, 3, m_op);
        m_cs->fromRgbA16(m_buffer.constData(), dst, nPixels);
    }

private:
    const KoColorSpace *m_cs;
    Layout m_layout;
    Op m_op;
    mutable QVector<quint8> m_buffer;        // one transformation object per thread
};

/// Multiplies the alpha of the pixels, never changes their colour.
/// Op: float operator()(const float rgb[3]) const - the opacity factor 0..1.
template<typename Op>
class PsAlphaTransformation : public KoColorTransformation
{
public:
    PsAlphaTransformation(const KoColorSpace *cs, const Op &op)
        : m_cs(cs), m_layout(layoutOf(cs)), m_op(op)
    {
    }

    void transform(const quint8 *src, quint8 *dst, qint32 nPixels) const override
    {
        if (nPixels <= 0) return;
        switch (m_layout) {
        case BgrU8:
            ps::runAlpha<quint8>(src, dst, nPixels, 2, 1, 0, 3, m_op);
            return;
        case BgrU16:
            ps::runAlpha<quint16>(src, dst, nPixels, 2, 1, 0, 3, m_op);
            return;
        case RgbF16:
            ps::runAlpha<ps::Half>(src, dst, nPixels, 0, 1, 2, 3, m_op);
            return;
        case RgbF32:
            ps::runAlpha<float>(src, dst, nPixels, 0, 1, 2, 3, m_op);
            return;
        case Other:
            break;
        }
        const int pixelSize = m_cs->pixelSize();
        m_buffer.resize(nPixels * 8);
        m_cs->toRgbA16(src, m_buffer.data(), nPixels);
        if (src != dst) memcpy(dst, src, size_t(nPixels) * pixelSize);
        const quint16 *p = reinterpret_cast<const quint16 *>(m_buffer.constData());
        for (int i = 0; i < nPixels; i++, p += 4) {
            if (p[3] == 0) continue;
            const float rgb[3] = {p[2] / 65535.0f, p[1] / 65535.0f, p[0] / 65535.0f};
            const float f = m_op(rgb);
            if (f < 1.0f) {
                quint8 *pixel = dst + i * pixelSize;
                m_cs->setOpacity(pixel, m_cs->opacityF(pixel) * qreal(f), 1);
            }
        }
    }

private:
    const KoColorSpace *m_cs;
    Layout m_layout;
    Op m_op;
    mutable QVector<quint8> m_buffer;
};

struct LutOp {
    QSharedPointer<const ps::Lut> lut;
    bool linear;
    void operator()(float *rgb) const { ps::applyLut(*lut, rgb, linear); }
};

struct SelectiveColorOp {
    ps::SelectiveColor settings;
    void operator()(float *rgb) const { ps::selectiveColor(settings, rgb); }
};

struct BlendIfOp {
    ps::BlendIf settings;
    float operator()(const float *rgb) const { return ps::blendIfFactor(settings, rgb); }
};

/// Used when the configuration is empty or broken: pixels pass unchanged.
class PsIdentityTransformation : public KoColorTransformation
{
public:
    explicit PsIdentityTransformation(const KoColorSpace *cs) : m_pixelSize(cs->pixelSize()) {}

    void transform(const quint8 *src, quint8 *dst, qint32 nPixels) const override
    {
        if (src != dst && nPixels > 0) memcpy(dst, src, size_t(nPixels) * m_pixelSize);
    }

private:
    int m_pixelSize;
};

// ---- parsed LUTs, shared between threads and filter invocations
typedef QSharedPointer<const ps::Lut> LutSP;

LutSP lutFromConfig(const KisFilterConfigurationSP config)
{
    static QMutex mutex;
    static QHash<QByteArray, LutSP> cache;
    static QList<QByteArray> order;

    if (!config) return LutSP();

    // "lut_id" (set by the ps_tools plugin: a hash of the table) finds a
    // parsed table without decoding the - possibly megabytes long - text
    // again for every tile that is rendered
    const QString id = config->getString("lut_id");
    QByteArray idKey;
    if (!id.isEmpty()) {
        idKey = QByteArray("id:");
        idKey.append(id.toLatin1());
        QMutexLocker l(&mutex);
        QHash<QByteArray, LutSP>::const_iterator it = cache.constFind(idKey);
        if (it != cache.constEnd()) return it.value();
    }

    const QVariant value = config->getProperty("lut");
    QByteArray packed = value.toByteArray();
    if (value.type() == QVariant::String) {
        packed = QByteArray::fromBase64(value.toString().toLatin1());
    }
    if (packed.isEmpty()) return LutSP();

    const QByteArray key = idKey.isEmpty() ? QCryptographicHash::hash(packed, QCryptographicHash::Sha1)
                                           : idKey;
    {
        QMutexLocker l(&mutex);
        QHash<QByteArray, LutSP>::const_iterator it = cache.constFind(key);
        if (it != cache.constEnd()) return it.value();
    }

    const QByteArray raw = qUncompress(packed);
    QSharedPointer<ps::Lut> lut(new ps::Lut());
    LutSP result;
    if (!raw.isEmpty() &&
        ps::parseLut(reinterpret_cast<const uint8_t *>(raw.constData()), size_t(raw.size()), *lut)) {
        result = lut;
    }

    QMutexLocker l(&mutex);
    cache.insert(key, result);              // failures are remembered too
    order.append(key);
    while (order.size() > 8) cache.remove(order.takeFirst());
    return result;
}

} // namespace

// ----------------------------------------------------------- Color Lookup
KisFilterPsColorLookup::KisFilterPsColorLookup()
    : KisColorTransformationFilter(id(), FiltersCategoryAdjustId, i18n("Color Lookup (PS)"))
{
    setColorSpaceIndependence(FULLY_INDEPENDENT);
    setSupportsPainting(false);
    setSupportsAdjustmentLayers(true);
    setShowConfigurationWidget(false);
    setSupportsLevelOfDetail(true);
}

KoColorTransformation *KisFilterPsColorLookup::createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const
{
    const LutSP lut = lutFromConfig(config);
    if (!lut) return new PsIdentityTransformation(cs);
    LutOp op;
    op.lut = lut;
    op.linear = config->getBool("linear", false);
    return new PsColorTransformation<LutOp>(cs, op);
}

// -------------------------------------------------------- Selective Color
KisFilterPsSelectiveColor::KisFilterPsSelectiveColor()
    : KisColorTransformationFilter(id(), FiltersCategoryAdjustId, i18n("Selective Color (PS)"))
{
    setColorSpaceIndependence(FULLY_INDEPENDENT);
    setSupportsPainting(false);
    setSupportsAdjustmentLayers(true);
    setShowConfigurationWidget(false);
    setSupportsLevelOfDetail(true);
}

KoColorTransformation *KisFilterPsSelectiveColor::createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const
{
    float values[36];
    for (int i = 0; i < 36; i++) values[i] = 0.0f;
    bool relative = true;
    if (config) {
        const QStringList parts = config->getString("values").split(',');
        for (int i = 0; i < 36 && i < parts.size(); i++) {
            bool ok = false;
            const float v = parts[i].trimmed().toFloat(&ok);
            if (ok) values[i] = v / 100.0f;            // percent
        }
        relative = config->getBool("relative", true);
    }
    ps::SelectiveColor s;
    s.set(values, relative);
    if (!s.any) return new PsIdentityTransformation(cs);
    SelectiveColorOp op;
    op.settings = s;
    return new PsColorTransformation<SelectiveColorOp>(cs, op);
}

// ---------------------------------------------------------------- Blend If
KisFilterPsBlendIf::KisFilterPsBlendIf()
    : KisColorTransformationFilter(id(), FiltersCategoryOtherId, i18n("Blend If: This Layer (PS)"))
{
    setColorSpaceIndependence(FULLY_INDEPENDENT);
    setSupportsPainting(false);
    setSupportsAdjustmentLayers(true);
    setShowConfigurationWidget(false);
    setSupportsLevelOfDetail(true);
}

KoColorTransformation *KisFilterPsBlendIf::createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const
{
    ps::BlendIf b;
    if (config) {
        b.channel = qBound(0, config->getInt("channel", 0), 3);
        b.b0 = float(qBound(0, config->getInt("black0", 0), 255));
        b.b1 = float(qBound(0, config->getInt("black1", 0), 255));
        b.w0 = float(qBound(0, config->getInt("white0", 255), 255));
        b.w1 = float(qBound(0, config->getInt("white1", 255), 255));
        if (b.b1 < b.b0) b.b1 = b.b0;
        if (b.w1 < b.w0) b.w1 = b.w0;
    }
    if (b.neutral()) return new PsIdentityTransformation(cs);
    BlendIfOp op;
    op.settings = b;
    return new PsAlphaTransformation<BlendIfOp>(cs, op);
}

// --------------------------------------------------------- Camera Raw
namespace {

ps::cr::Settings cameraRawSettings(const KisFilterConfigurationSP config)
{
    ps::cr::Settings s;
    if (!config) return s;
    int count = 0;
    const char *const *names = ps::cr::settingNames(count);
    for (int i = 0; i < count; i++) {
        QVariant v;
        if (config->getProperty(QString::fromLatin1(names[i]), v) && v.isValid()) {
            bool ok = false;
            const float f = v.toFloat(&ok);
            if (ok) ps::cr::setValue(s, names[i], f);
        }
    }
    const char *const *curves = ps::cr::curveNames(count);
    for (int i = 0; i < count; i++) {
        const QString text = config->getString(QString::fromLatin1(curves[i]));
        if (!text.isEmpty()) ps::cr::setCurve(s, curves[i], text.toStdString());
    }
    return s;
}

/// Pixels of a colour space <-> the kernel's planes (linear light).
class CameraRawPixels
{
public:
    explicit CameraRawPixels(const KoColorSpace *cs) : m_cs(cs), m_layout(layoutOf(cs))
    {
        const KoColorProfile *profile = cs->profile();
        m_linear = (m_layout == RgbF16 || m_layout == RgbF32) && profile && profile->isLinear();
    }

    void read(const QVector<quint8> &raw, int n, ps::cr::Buffer &buf)
    {
        buf.r.resize(n); buf.g.resize(n); buf.b.resize(n); buf.a.resize(n);
        const quint8 *src = raw.constData();
        if (m_layout == Other) {
            m_tmp.resize(n * 8);
            m_cs->toRgbA16(src, m_tmp.data(), n);
            src = m_tmp.constData();
        }
        for (int i = 0; i < n; i++) {
            float r, g, b, a;
            fetch(src, i, r, g, b, a);
            buf.r[i] = m_linear ? r : ps::cr::srgbDecode(r);
            buf.g[i] = m_linear ? g : ps::cr::srgbDecode(g);
            buf.b[i] = m_linear ? b : ps::cr::srgbDecode(b);
            buf.a[i] = a;
        }
    }

    /// Writes the inner rect (offset ox, oy in the buffer) into raw.
    void write(const ps::cr::Buffer &buf, int ox, int oy, int w, int h, QVector<quint8> &raw)
    {
        const int n = w * h;
        const bool other = m_layout == Other;
        const int pixelSize = other ? 8 : m_cs->pixelSize();
        QVector<quint8> out(n * pixelSize);
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                const size_t i = size_t(oy + y) * buf.width + (ox + x);
                float r = buf.r[i], g = buf.g[i], b = buf.b[i];
                if (!m_linear) {
                    r = ps::cr::srgbEncode(std::max(0.0f, r));
                    g = ps::cr::srgbEncode(std::max(0.0f, g));
                    b = ps::cr::srgbEncode(std::max(0.0f, b));
                }
                store(out.data(), y * w + x, r, g, b, buf.a[i]);
            }
        }
        if (other) {
            raw.resize(n * m_cs->pixelSize());
            m_cs->fromRgbA16(out.constData(), raw.data(), n);
        } else {
            raw = out;
        }
    }

private:
    void fetch(const quint8 *p, int i, float &r, float &g, float &b, float &a) const
    {
        switch (m_layout) {
        case BgrU8: {
            const quint8 *q = p + i * 4;
            b = q[0] / 255.0f; g = q[1] / 255.0f; r = q[2] / 255.0f; a = q[3] / 255.0f;
            return;
        }
        case RgbF16: {
            const quint16 *q = reinterpret_cast<const quint16 *>(p) + i * 4;
            r = ps::halfToFloat(q[0]); g = ps::halfToFloat(q[1]); b = ps::halfToFloat(q[2]);
            a = ps::halfToFloat(q[3]);
            return;
        }
        case RgbF32: {
            const float *q = reinterpret_cast<const float *>(p) + i * 4;
            r = q[0]; g = q[1]; b = q[2]; a = q[3];
            return;
        }
        default: {                              // BgrU16 and the RGBA16 conversion
            const quint16 *q = reinterpret_cast<const quint16 *>(p) + i * 4;
            b = q[0] / 65535.0f; g = q[1] / 65535.0f; r = q[2] / 65535.0f; a = q[3] / 65535.0f;
            return;
        }
        }
    }

    void store(quint8 *p, int i, float r, float g, float b, float a) const
    {
        auto u8 = [](float v) { return quint8(qBound(0.0f, v, 1.0f) * 255.0f + 0.5f); };
        auto u16 = [](float v) { return quint16(qBound(0.0f, v, 1.0f) * 65535.0f + 0.5f); };
        switch (m_layout) {
        case BgrU8: {
            quint8 *q = p + i * 4;
            q[0] = u8(b); q[1] = u8(g); q[2] = u8(r); q[3] = u8(a);
            return;
        }
        case RgbF16: {
            quint16 *q = reinterpret_cast<quint16 *>(p) + i * 4;
            q[0] = ps::floatToHalf(r); q[1] = ps::floatToHalf(g); q[2] = ps::floatToHalf(b);
            q[3] = ps::floatToHalf(a);
            return;
        }
        case RgbF32: {
            float *q = reinterpret_cast<float *>(p) + i * 4;
            q[0] = r; q[1] = g; q[2] = b; q[3] = a;
            return;
        }
        default: {
            quint16 *q = reinterpret_cast<quint16 *>(p) + i * 4;
            q[0] = u16(b); q[1] = u16(g); q[2] = u16(r); q[3] = u16(a);
            return;
        }
        }
    }

    const KoColorSpace *m_cs;
    Layout m_layout;
    bool m_linear = false;
    QVector<quint8> m_tmp;
};

int lodOf(KisPaintDeviceSP device)
{
    return device && device->defaultBounds() ? device->defaultBounds()->currentLevelOfDetail() : 0;
}

} // namespace

KisFilterPsCameraRaw::KisFilterPsCameraRaw()
    : KisFilter(id(), FiltersCategoryAdjustId, i18n("Camera Raw Filter (PS)"))
{
    setColorSpaceIndependence(FULLY_INDEPENDENT);
    setSupportsPainting(false);
    setSupportsAdjustmentLayers(true);
    setSupportsThreading(true);
    setShowConfigurationWidget(false);
    setSupportsLevelOfDetail(true);
}

QRect KisFilterPsCameraRaw::neededRect(const QRect &rect, const KisFilterConfigurationSP config, int lod) const
{
    const int b = ps::cr::border(cameraRawSettings(config), lod);
    return rect.adjusted(-b, -b, b, b);
}

QRect KisFilterPsCameraRaw::changedRect(const QRect &rect, const KisFilterConfigurationSP config, int lod) const
{
    const int b = ps::cr::border(cameraRawSettings(config), lod);
    return rect.adjusted(-b, -b, b, b);
}

void KisFilterPsCameraRaw::processImpl(KisPaintDeviceSP device, const QRect &applyRect,
                                       const KisFilterConfigurationSP config, KoUpdater *progressUpdater) const
{
    if (!device || applyRect.isEmpty()) return;
    const ps::cr::Settings s = cameraRawSettings(config);
    if (s.isIdentity()) {
        if (progressUpdater) progressUpdater->setProgress(100);
        return;
    }
    const int lod = lodOf(device);
    const int b = ps::cr::border(s, lod);
    const QRect area = applyRect.adjusted(-b, -b, b, b);

    const KoColorSpace *cs = device->colorSpace();
    QVector<quint8> raw(area.width() * area.height() * cs->pixelSize());
    device->readBytes(raw.data(), area);

    ps::cr::Buffer buf;
    buf.width = area.width();
    buf.height = area.height();
    buf.x0 = area.x();
    buf.y0 = area.y();
    CameraRawPixels pixels(cs);
    pixels.read(raw, buf.width * buf.height, buf);

    ps::cr::Frame frame;
    QRect bounds = device->defaultBounds() ? device->defaultBounds()->bounds() : QRect();
    if (bounds.isEmpty() || bounds.width() > 1000000) bounds = device->exactBounds();
    if (bounds.isEmpty()) bounds = applyRect;
    frame.x = bounds.x();
    frame.y = bounds.y();
    frame.width = bounds.width();
    frame.height = bounds.height();
    frame.lod = lod;

    ps::cr::ToneLuts tone;
    tone.build(s);
    if (progressUpdater) progressUpdater->setProgress(10);
    ps::cr::process(s, buf, frame, tone);
    if (progressUpdater) progressUpdater->setProgress(90);

    QVector<quint8> out;
    pixels.write(buf, applyRect.x() - area.x(), applyRect.y() - area.y(), applyRect.width(),
                 applyRect.height(), out);
    device->writeBytes(out.constData(), applyRect);
    if (progressUpdater) progressUpdater->setProgress(100);
}

#include "ps_filters.moc"
