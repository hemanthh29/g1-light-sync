# Mitsuko G1 for iOS

Native SwiftUI companion for controlling Mitsuko G1 over the same Wi-Fi network. It discovers the laptop through Bonjour (`_mitsukog1._tcp`), so no laptop address is typed. First-time pairing uses the laptop's one-time code; the app stores its access token in the iOS Keychain and reconnects automatically afterward.

The app includes screen sync, music reactive modes, stop and lights off, brightness, color presets, lighting effects, and saved profiles. The phone talks directly to the laptop bridge; light control remains on the laptop.

The iPhone asks for Local Network access the first time it searches for the laptop. Allow that prompt. Both devices must be on the same Wi-Fi network, and Mitsuko G1 must be open on the laptop. Bonjour support on the laptop is provided by Avahi.

## Build and install without owning a Mac

This project includes `.github/workflows/ios-unsigned-build.yml`. A GitHub Actions macOS runner can compile an unsigned IPA; the source itself does not contain signing credentials. Once this folder is in a private GitHub repository, run **Actions → Build iOS IPA → Run workflow** and download the `MitsukoG1-unsigned-IPA` artifact.

On the Linux laptop, install the official [iLoader](https://github.com/nab138/iloader/releases/latest) release and `usbmuxd`, connect the iPhone by USB, unlock it, and tap **Trust**. iLoader can sign and install an IPA using your Apple Account. This uses your Apple Account on your own computer; do not send its password to anyone. On iOS 16 and later, turn on Developer Mode. A free Apple Account signing profile expires after seven days, so use SideStore to refresh the installed app or reinstall with iLoader when it expires. SideStore requires the LocalDevVPN app while refreshing; see its [official Linux setup instructions](https://github.com/SideStore/SideStore-Docs/blob/main/docs/installation/prerequisites.mdx).

For first pairing, open Mitsuko G1 on the laptop and choose **Phone Wi-Fi setup** to see the current code. In the iPhone app, tap **Pair** and enter that code once. No IP address is entered. When installing the updated Linux package on another laptop, install the recommended `avahi-daemon` package so iOS Bonjour discovery is available.
