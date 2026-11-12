/*
 *  Photoshop-style filters for Krita: Color Lookup (3D / 1D LUT), Selective
 *  Color and Blend If ("This Layer"). Real Krita filters: usable in filter
 *  layers and filter masks, tiled, multithreaded, saved in the .kra.
 *  They have no configuration widget of their own; the ps_tools Python
 *  plugin is their user interface.
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef PS_FILTERS_H
#define PS_FILTERS_H

#include <QObject>
#include <QVariant>

#include <klocalizedstring.h>

#include "filter/kis_color_transformation_filter.h"
#include "filter/kis_filter.h"

class KritaPsFilters : public QObject
{
    Q_OBJECT
public:
    KritaPsFilters(QObject *parent, const QVariantList &);
    ~KritaPsFilters() override;
};

/// Properties: "lut" (byte array: qCompress'ed "PSL1" table, see ps_kernels.h,
/// or that as base64 text), "lut_id" (optional: any text that changes with the
/// table, used as the cache key), "linear" (bool), "name" (string, for the
/// user interface only).
class KisFilterPsColorLookup : public KisColorTransformationFilter
{
public:
    KisFilterPsColorLookup();
    KoColorTransformation *createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const override;
    static inline KoID id() { return KoID("pscolorlookup", i18n("Color Lookup (PS)")); }
};

/// Properties: "values" (string: 36 comma separated percentages, 9 ranges x
/// CMYK), "relative" (bool, default true).
class KisFilterPsSelectiveColor : public KisColorTransformationFilter
{
public:
    KisFilterPsSelectiveColor();
    KoColorTransformation *createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const override;
    static inline KoID id() { return KoID("psselectivecolor", i18n("Selective Color (PS)")); }
};

/// Properties: "channel" (0 gray, 1 red, 2 green, 3 blue), "black0",
/// "black1", "white0", "white1" (0..255). Lowers the alpha of the pixels.
class KisFilterPsBlendIf : public KisColorTransformationFilter
{
public:
    KisFilterPsBlendIf();
    KoColorTransformation *createTransformation(const KoColorSpace *cs, const KisFilterConfigurationSP config) const override;
    static inline KoID id() { return KoID("psblendif", i18n("Blend If: This Layer (PS)")); }
};

/// Camera Raw Filter: the Light, Color, Effects and Detail panels of Adobe
/// Camera Raw (ps_cameraraw.h). Properties: the slider names of
/// ps::cr::settingNames() (numbers) and curve_rgb / curve_red / curve_green /
/// curve_blue ("x,y;x,y;..." in 0..1). Highlights / Shadows, Clarity,
/// Texture, Dehaze, sharpening and noise reduction look at the pixels around,
/// so the filter asks Krita for a border of context (neededRect).
class KisFilterPsCameraRaw : public KisFilter
{
public:
    KisFilterPsCameraRaw();
    void processImpl(KisPaintDeviceSP device, const QRect &applyRect, const KisFilterConfigurationSP config,
                     KoUpdater *progressUpdater) const override;
    QRect neededRect(const QRect &rect, const KisFilterConfigurationSP config, int lod) const override;
    QRect changedRect(const QRect &rect, const KisFilterConfigurationSP config, int lod) const override;
    static inline KoID id() { return KoID("pscameraraw", i18n("Camera Raw Filter (PS)")); }
};

#endif
