# Mitsuko G1

Mitsuko G1 controls the Phantom G1 light strip from Linux, Android, and iOS.

## Repository layout

- `deb/` — Linux desktop application source payload and matching Debian package metadata.
- `android/` — Android companion source project.
- `ios/` — iOS companion source project.

## Releases

Each platform has separate GitHub Releases with its package and SHA-256 checksum:

- Linux DEB: `deb-1.0.10-12`
- Android APK: `android-1.3`
- iOS IPA: `ios-1.1-build3` (unsigned; sign/install with SideStore or another iOS sideloading workflow)

## Build versions

- Debian package: 1.0.10-12
- Android: 1.3, version code 4
- iOS: 1.1, build 3; deployment target iOS 16+

The desktop source tree is the source payload shipped in the matching DEB. Its Debian control files and maintainer scripts are under `deb/DEBIAN/`.

Linux-only feature: the desktop app can flash the selected palette color for desktop notifications when the light state is known and no live sync is running. Notification text is not stored. Android and iOS are unchanged.
