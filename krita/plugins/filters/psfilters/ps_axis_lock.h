/*
 *  Photoshop-like Shift + drag with the brush (ps_tools): the stroke is
 *  locked to a horizontal or vertical line, chosen by the direction of the
 *  first few pixels of the movement.
 *
 *  This is the C++ version of the ps_tools plugin's axislock.py. The Python
 *  version has to see every event of the application while the brush tool
 *  is active, which costs time on every pen move; here the same filter is a
 *  few integer compares per event. The plugin switches it on and off with
 *  the application property "ps_tools_axislock" (its "Shift + drag locks
 *  the brush to an axis" option) and then leaves the events alone.
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef PS_AXIS_LOCK_H
#define PS_AXIS_LOCK_H

#include <QElapsedTimer>
#include <QObject>
#include <QPointF>
#include <QPointer>

class QWidget;
class QEvent;

class PsAxisLock : public QObject
{
public:
    explicit PsAxisLock(QObject *parent = nullptr);
    bool eventFilter(QObject *obj, QEvent *ev) override;

    /// "x" / "y" axis, or 0 while undecided (exposed for tests)
    static QPointF constrain(const QPointF &origin, const QPointF &pos, char &axis, qreal threshold = 4.0);

private:
    bool handle(QObject *obj, QEvent *ev);
    static bool isCanvas(QObject *obj);
    static bool brushActive();
    static bool shiftOnly(Qt::KeyboardModifiers eventMods);
    static bool enabled();

    struct Stroke {
        QPointer<QWidget> canvas;
        bool tablet = false;
        QPointF origin;
        char axis = 0;
        bool active = false;
    } m_stroke;
    bool m_sending = false;
    QElapsedTimer m_clock;
    qint64 m_lastPen = -1000000;
};

#endif
