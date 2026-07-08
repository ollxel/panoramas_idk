# Panorama POI Overlay Devlog

## Problem description

The current panorama POI overlay implementation is broken for vertical camera motion. Markers are rendered using a DOM overlay in `static/js/map.js`, but their vertical position is effectively fixed and does not follow the viewer pitch correctly.

## Current implementation

- `ensurePoiOverlay(container)` creates a `div.panorama-poi-overlay` on top of the panorama container.
- `renderPoiOverlay(items)` generates marker buttons for each visible POI and stores them in `currentPoiItems`.
- `updatePoiOverlay()` updates marker positions every frame.

### Horizontal behavior

The horizontal logic is correct:
- `const yaw = _normalizeAngle(viewViewer.getYaw());`
- `const hfov = viewViewer.getHfov();`
- `diff = item.bearing - yaw;`
- `const visible = Math.abs(diff) <= hfov / 2;`
- `const x = width / 2 + (diff / hfov) * width;`

This means markers are shown only when their bearing is inside the current horizontal field of view.

### Vertical behavior bug

The vertical coordinate is currently hardcoded and effectively static:
- `const y = Math.max(42, height * 0.10);`

That means POI markers always stay near the top-center of the screen even when the camera is tilted up or down. The expected behavior is:

- markers should remain fixed in world-relative screen space,
- when the user looks up, markers that correspond to objects below the horizon should move downward and eventually disappear below the bottom of the overlay,
- when the user looks down, markers that correspond to objects above the horizon should move upward and eventually disappear above the top.

## Attempts and changes

I already tried adding a pitch check in `updatePoiOverlay()` using `viewViewer.getPitch()`:

- if `Math.abs(pitch)` exceeded a threshold, all markers were hidden,
- I also tried a `pitchOffset` shift that changed `y` by some fraction of the viewport height.

That helped a little, but the markers were still not behaving naturally because the overlay y-position was still derived from a fixed base Y, and not from a proper projection of the camera pitch.

## Why it still fails

The code is missing a proper vertical mapping strategy. In a spherical panorama viewer like Pannellum, the vertical position of a world-fixed POI should be determined by the camera pitch and the vertical field of view / elevation angle, not by a constant screen offset.

## Recommended fix direction

The next fix should:

1. compute a dynamic vertical projection based on viewer pitch and an effective vertical FOV or screen mapping,
2. keep markers pinned to a world-space direction rather than a fixed top-offset,
3. hide markers when their calculated screen Y crosses the top or bottom bounds of the overlay,
4. keep horizontal visibility logic unchanged.

## Relevant code references

- `static/js/map.js`:
  - `ensurePoiOverlay(container)`
  - `renderPoiOverlay(items)`
  - `updatePoiOverlay()`
  - `focusPoiYaw(angle)`
- `static/css/style.css`:
  - `.panorama-poi-overlay`
  - `.pano-poi-marker`
  - `.pano-poi-tooltip`

## Status

The issue is not a marker creation bug, but a coordinate mapping bug. The overlay markers are being rendered, but their vertical movement is wrong, causing them to look glued to one position instead of following the camera pitch.

This should be fixed by replacing the hardcoded `y` computation in `updatePoiOverlay()` with a pitch-dependent projection and bounds check.
