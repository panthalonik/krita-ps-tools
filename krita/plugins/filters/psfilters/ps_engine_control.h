/*
 *  Control of the Krita PS engine speed-ups (kis_ps_perf.h) from Python.
 *
 *  Python has no direct way to the engine's C++ functions, so this filter
 *  watches two dynamic properties of the application object:
 *
 *    "ps_tools_perf"  (int)     - the speed-up switches (psPerf::Flag); the
 *                                 ps_tools plugin sets it from its option.
 *    "ps_tools_test"  (string)  - commands for the tests and benchmarks in
 *                                 ci/smoke/ps_smoke.py; the answer is put in
 *                                 "ps_tools_test_result". Commands may start
 *                                 with "#<n> " (to make every value new):
 *        stats | notes | reset_stats | drop | perf <flags>
 *        dab <node uuid> <x> <y> <w> <h> <seed>
 *            fill a rect of the layer with a colour made from the seed and
 *            update it (what a brush dab does)
 *        dabs <node uuid> <count> <x> <y> <w> <h> <dx> <dy> <seed>
 *            <count> dabs along a line, without waiting (a stroke)
 *        dirty <node uuid>     - update the whole layer
 *        priority <x> <y> <w> <h> - the "visible" area of every open image
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef PS_ENGINE_CONTROL_H
#define PS_ENGINE_CONTROL_H

#include <QObject>
#include <QString>

class PsEngineControl : public QObject
{
public:
    explicit PsEngineControl(QObject *parent = nullptr);
    bool eventFilter(QObject *obj, QEvent *ev) override;

    static QString run(const QString &command);

private:
    bool m_busy = false;
};

#endif
