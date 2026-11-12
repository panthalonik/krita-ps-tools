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
#include "ps_axis_lock.h"

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
static const int PS_TOOLS_ENGINE_LEVEL = 5;

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

#include "ps_filters.moc"
