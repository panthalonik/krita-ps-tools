/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "ps_engine_control.h"

#include <QColor>
#include <QCoreApplication>
#include <QDynamicPropertyChangeEvent>
#include <QPointer>
#include <QStringList>
#include <QUuid>

#include <KoColor.h>

#include <KisDocument.h>
#include <KisPart.h>
#include <kis_image.h>
#include <kis_layer_utils.h>
#include <kis_node.h>
#include <kis_paint_device.h>
#include <kis_ps_perf.h>

PsEngineControl::PsEngineControl(QObject *parent)
    : QObject(parent)
{
}

namespace {

KisNodeSP findNode(const QString &uuidText)
{
    const QUuid uuid(uuidText);
    if (uuid.isNull()) return KisNodeSP();
    Q_FOREACH (QPointer<KisDocument> doc, KisPart::instance()->documents()) {
        if (!doc || !doc->image()) continue;
        KisNodeSP node = KisLayerUtils::findNodeByUuid(doc->image()->root(), uuid);
        if (node) return node;
    }
    return KisNodeSP();
}

/// A colour made from the seed (stable for the tests), partly transparent.
KoColor seedColor(int seed, const KoColorSpace *cs)
{
    const int h = (seed * 47) % 360;
    const int s = 120 + (seed * 31) % 130;
    const int v = 90 + (seed * 17) % 160;
    const int a = 70 + (seed * 53) % 186;
    return KoColor(QColor::fromHsv(h, s, v, a), cs);
}

void dab(KisNodeSP node, const QRect &rect, int seed)
{
    KisPaintDeviceSP dev = node->paintDevice();
    if (!dev || rect.isEmpty()) return;
    dev->fill(rect, seedColor(seed, dev->colorSpace()));
    // a second, smaller patch: more than one colour per dab
    const QRect inner = rect.adjusted(rect.width() / 4, rect.height() / 4,
                                      -rect.width() / 4, -rect.height() / 4);
    if (!inner.isEmpty()) {
        dev->fill(inner, seedColor(seed + 1, dev->colorSpace()));
    }
    node->setDirty(rect);
}

int num(const QStringList &args, int i)
{
    return i < args.size() ? args[i].toInt() : 0;
}

} // namespace

QString PsEngineControl::run(const QString &command)
{
    QStringList args = command.split(' ', Qt::SkipEmptyParts);
    if (!args.isEmpty() && args[0].startsWith('#')) args.removeFirst();
    if (args.isEmpty()) return "error: empty";
    const QString cmd = args[0];

    if (cmd == "stats") return psPerf::stats();
    if (cmd == "notes") return "notes: " + psPerf::notes();
    if (cmd == "reset_stats") {
        psPerf::resetStats();
        return "ok";
    }
    if (cmd == "drop") {
        psPerf::dropCaches();
        return "ok";
    }
    if (cmd == "perf") {
        psPerf::setFlags(num(args, 1));
        return QString("ok %1").arg(psPerf::flags());
    }
    if (cmd == "priority") {
        const QRect rc(num(args, 1), num(args, 2), num(args, 3), num(args, 4));
        Q_FOREACH (QPointer<KisDocument> doc, KisPart::instance()->documents()) {
            if (doc && doc->image()) doc->image()->setPsPriorityRect(rc);
        }
        return "ok";
    }
    if (cmd == "dab" || cmd == "dabs" || cmd == "dirty") {
        KisNodeSP node = findNode(args.value(1));
        if (!node) return "error: no node " + args.value(1);
        if (cmd == "dirty") {
            node->setDirty();
            return "ok";
        }
        if (cmd == "dab") {
            dab(node, QRect(num(args, 2), num(args, 3), num(args, 4), num(args, 5)), num(args, 6));
            return "ok";
        }
        const int count = num(args, 2);
        QRect rc(num(args, 3), num(args, 4), num(args, 5), num(args, 6));
        const int dx = num(args, 7), dy = num(args, 8), seed = num(args, 9);
        for (int i = 0; i < count; i++) {
            dab(node, rc, seed + i);
            rc.translate(dx, dy);
        }
        return "ok";
    }
    return "error: unknown command " + cmd;
}

bool PsEngineControl::eventFilter(QObject *obj, QEvent *ev)
{
    if (ev->type() != QEvent::DynamicPropertyChange || obj != QCoreApplication::instance() || m_busy) {
        return false;
    }
    const QByteArray name = static_cast<QDynamicPropertyChangeEvent *>(ev)->propertyName();
    if (name == "ps_tools_perf") {
        const QVariant v = obj->property("ps_tools_perf");
        if (v.isValid()) psPerf::setFlags(v.toInt());
    } else if (name == "ps_tools_test") {
        const QVariant v = obj->property("ps_tools_test");
        if (v.isValid()) {
            m_busy = true;
            obj->setProperty("ps_tools_test_result", run(v.toString()));
            m_busy = false;
        }
    }
    return false;
}
