# Mitsuko G1

Mitsuko G1 controls the Phantom G1 light strip from Linux, Android, and iOS.

## Repository layout

- `deb/` — Linux desktop application source payload and matching Debian package metadata.
- `android/` — Android companion source project.
- `ios/` — iOS companion source project.

## Releases

Each platform has separate GitHub Releases with its package and SHA-256 checksum:

- Linux DEB: `deb-1.0.10-30`
- Android APK: `android-1.3`
- iOS IPA: `ios-1.1-build4` (unsigned; sign/install with SideStore or another iOS sideloading workflow)

## Build versions

- Debian package: 1.0.10-30
- Android: 1.3, version code 4
- iOS: 1.1, build 4; deployment target iOS 16+

The desktop source tree is the source payload shipped in the matching DEB. Its Debian control files and maintainer scripts are under `deb/DEBIAN/`.

Linux-only features: Lighting effects includes a solid-color card with preset swatches, a custom picker, and an Apply button. Quick controls has an opt-in notification flash switch with single pop, double pulse, soft breathe, perimeter ping, and urgent triple patterns. Choose an alert color or preview the selected pattern from the same card. Patterns run only while the strip is off or showing a steady color, then restore that state; active effects and sync modes are left alone. The phone Wi-Fi relay runs independently as a per-user background service with an on/off switch in Phone Wi-Fi setup. The color chooser uses the app theme and supports hue, shade, and hex entry. Reopening the launcher restores the existing hidden window instead of starting another copy. Android and iOS are unchanged.
