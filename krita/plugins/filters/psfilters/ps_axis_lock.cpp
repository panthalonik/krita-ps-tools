/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "ps_axis_lock.h"

#include <QApplication>
#include <QGuiApplication>
#include <QMouseEvent>
#include <QTabletEvent>
#include <QWidget>
#include <QtMath>

#include <KoToolManager.h>

namespace {
const qint64 PEN_GRACE_MS = 600;    // after a pen event, mouse events are Windows' copies of it

bool isTabletType(QEvent::Type t)
{
    return t == QEvent::TabletPress || t == QEvent::TabletMove || t == QEvent::TabletRelease;
}

bool isPressType(QEvent::Type t)
{
    return t == QEvent::TabletPress || t == QEvent::MouseButtonPress;
}

bool isReleaseType(QEvent::Type t)
{
    return t == QEvent::TabletRelease || t == QEvent::MouseButtonRelease;
}
}

PsAxisLock::PsAxisLock(QObject *parent)
    : QObject(parent)
{
    m_clock.start();
}

QPointF PsAxisLock::constrain(const QPointF &origin, const QPointF &pos, char &axis, qreal threshold)
{
    const qreal dx = pos.x() - origin.x(), dy = pos.y() - origin.y();
    if (!axis) {
        if (qMax(qAbs(dx), qAbs(dy)) < threshold) return origin;
        axis = qAbs(dx) >= qAbs(dy) ? 'x' : 'y';
    }
    return axis == 'x' ? QPointF(pos.x(), origin.y()) : QPointF(origin.x(), pos.y());
}

bool PsAxisLock::enabled()
{
    return qApp && qApp->property("ps_tools_axislock").toBool();
}

bool PsAxisLock::isCanvas(QObject *obj)
{
    if (!obj || !obj->isWidgetType()) return false;
    const char *name = obj->metaObject()->className();
    return qstrcmp(name, "KisOpenGLCanvas2") == 0 || qstrcmp(name, "KisQPainterCanvas") == 0;
}

bool PsAxisLock::brushActive()
{
    return KoToolManager::instance()->activeToolId() == QLatin1String("KritaShape/KisToolBrush");
}

bool PsAxisLock::shiftOnly(Qt::KeyboardModifiers eventMods)
{
    const Qt::KeyboardModifiers mods = eventMods | QGuiApplication::queryKeyboardModifiers();
    return (mods & Qt::ShiftModifier) && !(mods & (Qt::ControlModifier | Qt::AltModifier | Qt::MetaModifier));
}

bool PsAxisLock::eventFilter(QObject *obj, QEvent *ev)
{
    if (m_sending) return false;
    switch (ev->type()) {
    case QEvent::TabletPress:
    case QEvent::TabletMove:
    case QEvent::TabletRelease:
    case QEvent::MouseButtonPress:
    case QEvent::MouseMove:
    case QEvent::MouseButtonRelease:
        break;
    default:
        return false;                       // everything else: nothing to do
    }
    if (!m_stroke.active && !isPressType(ev->type())) return false;
    return handle(obj, ev);
}

bool PsAxisLock::handle(QObject *obj, QEvent *ev)
{
    const QEvent::Type t = ev->type();
    const bool tablet = isTabletType(t);
    QTabletEvent *te = tablet ? static_cast<QTabletEvent *>(ev) : nullptr;
    QMouseEvent *me = tablet ? nullptr : static_cast<QMouseEvent *>(ev);
    const bool synth = me && me->source() != Qt::MouseEventNotSynthesized;
    const qint64 now = m_clock.elapsed();

    if (tablet) {
        m_lastPen = now;
    } else if ((m_stroke.active && m_stroke.tablet) || now - m_lastPen < PEN_GRACE_MS) {
        // Windows' mouse copy of a pen event: Krita drops those, so do we
        return false;
    }

    if (isPressType(t)) {
        m_stroke.active = false;
        const Qt::MouseButton button = tablet ? te->button() : me->button();
        const Qt::KeyboardModifiers mods = tablet ? te->modifiers() : me->modifiers();
        if (button == Qt::LeftButton && isCanvas(obj) && enabled() && shiftOnly(mods) && brushActive()) {
            m_stroke.canvas = static_cast<QWidget *>(obj);
            m_stroke.tablet = tablet;
            m_stroke.origin = tablet ? te->posF() : me->localPos();
            m_stroke.axis = 0;
            m_stroke.active = true;
        }
        return false;
    }

    if (!m_stroke.active || obj != m_stroke.canvas.data() || tablet != m_stroke.tablet) return false;
    if (me && !synth && t == QEvent::MouseMove && !(me->buttons() & Qt::LeftButton)) {
        m_stroke.active = false;            // missed the release
        return false;
    }

    const QPointF pos = tablet ? te->posF() : me->localPos();
    const QPointF locked = constrain(m_stroke.origin, pos, m_stroke.axis);
    if (isReleaseType(t)) m_stroke.active = false;
    if (locked == pos) return false;
    const QPointF delta = locked - pos;

    m_sending = true;
    if (tablet) {
        QTabletEvent copy(t, locked, te->globalPosF() + delta, te->deviceType(), te->pointerType(),
                          te->pressure(), te->xTilt(), te->yTilt(), te->tangentialPressure(),
                          te->rotation(), te->z(), te->modifiers(), te->uniqueId(),
                          te->button(), te->buttons());
        copy.setTimestamp(te->timestamp());
        QApplication::sendEvent(obj, &copy);
    } else {
        QMouseEvent copy(t, locked, me->windowPos() + delta, me->screenPos() + delta,
                         me->button(), me->buttons(), me->modifiers(), me->source());
        copy.setTimestamp(me->timestamp());
        QApplication::sendEvent(obj, &copy);
    }
    m_sending = false;
    ev->accept();
    return true;
}
