# LocalFlow for Android

A floating dot that dictates into any app on your phone, using the LocalFlow PC on your
Tailscale network to do the transcription. Nothing leaves your own devices.

> **Status.** Version 0.1.2. The app builds (`assembleRelease`, unit tests and lint pass locally
> and in CI) and 0.1.0 has been run on a Samsung Galaxy S23: the dot, waveform, colours and
> ✕/✓ work. The "only show the dot while typing" behaviour in 0.1.1 is new; if something
> misbehaves, see [What to report](#what-to-report) at the end. 0.1.2 changes nothing in the
> app itself: it is the first build signed with the permanent release key (see [Updating](#updating)).

## What you need

- A PC running LocalFlow with **phone access enabled** (`server.enabled: true` in
  `config.yaml`; see [API.md](API.md)).
- **Tailscale** installed and signed in on both the PC and the phone, on the same tailnet.
- An Android phone on **Android 8.0 or newer** (Android 13+ recommended; that is what it was
  written against).

## Install

1. On the phone, open the
   [Releases page](https://github.com/DatafyingTech/LocalFlow/releases) and download the
   newest `LocalFlow-android-v*.apk`.
2. Open the download. Android will say the phone is not allowed to install unknown apps from
   this source:
   - **Stock Android / Pixel:** tap *Settings* on that prompt → turn on *Allow from this source*
     (it is under Settings → Apps → Special app access → Install unknown apps → your browser).
   - **Samsung One UI:** Settings → Apps → *⋮* (top right) → Special access → Install unknown
     apps → pick your browser (or *My Files*) → *Allow from this source*.
3. Go back and tap **Install**. If Play Protect asks, choose *Install anyway*: the app is not on
   the Play Store and is signed with a development key.
4. Open **LocalFlow**.

## Updating

From 0.1.2 onward every release is signed with the same permanent key, so just download the
new APK and install it over the old one: Android treats it as an update and your Server URL,
token, switches and dot position are kept.

**If you have 0.1.0 or 0.1.1 installed, uninstall it once first** (long-press the LocalFlow
icon → Uninstall, or Settings → Apps → LocalFlow → Uninstall). Those two builds were signed
with throwaway keys that CI generated fresh on every run, so Android refuses to install anything
over them ("package conflicts with an existing package"). Uninstalling wipes the app's settings,
so after installing 0.1.2 paste the setup line again and re-grant the permissions. That is a
one-time chore; later updates install in place.

### Verify the download

Every LocalFlow Android release is signed with a certificate whose SHA-256 fingerprint is

```
1c8f3986f497d89747e0981712cb4c1d4fffb741b62dedf69f2113cb925ea013
```

To check an APK before installing it, run `apksigner verify --print-certs LocalFlow-android-v*.apk`
(apksigner is in the Android SDK's `build-tools/<version>/` folder) and compare the
`Signer #1 certificate SHA-256 digest` line with the value above. The same line is printed in
the "Verify the release signature" step of every CI run on GitHub, so you can also compare it
there. A different digest means the file is not one of ours.

## Connect it to your PC

1. On the PC, right-click the LocalFlow tray icon → **Phone setup** → **Copy setup line**.
   That copies `http://your-pc.your-tailnet.ts.net|<token>` to the PC clipboard.
2. Get that line onto the phone. Easiest: paste it into a note or a chat with yourself, then
   copy it on the phone. (If you use a clipboard sync such as Windows *Nearby Share*, KDE
   Connect or Samsung *Continue apps on other devices*, it is already there.)
3. In the app tap **Paste setup line**. The Server URL and Token fields fill in, a toast
   confirms which PC was picked up (`Setup line pasted: PC is your-pc.your-tailnet.ts.net`;
   the token is never shown) and a connection test runs. You should see something like
   `Connected: LocalFlow 0.1.1 · parakeet on GPU · gemma3:4b · ready`.
   - Or type them by hand: the URL is the PC's MagicDNS name shown in the tray, not the
     `100.x.y.z` address (see API.md for why).

**What happens while the address is blank.** The app ships with no PC address (the author's
tailnet name is not baked in), so until you paste the setup line:

- The setup screen shows a hint under the empty Server URL field: *On the PC: right-click the
  dot → Phone setup → Copy setup line, then press Paste setup line here.*
- Holding or tapping the dot does **not** record. The dot shows a red ring, a toast says
  *LocalFlow is not connected to your PC yet. Opening setup…*, and the app opens with the
  PC-connection card scrolled into view and highlighted. The same happens when the token is blank.
- **Test connection** says *No PC address set. Paste the setup line from your PC.* A typed
  address that is not a URL gets *That address does not look right. Expected
  http://pc-name.tailnet.ts.net* instead.

**Diagnose.** Next to the two buttons is **Diagnose**. It runs the same health call as *Test
connection* but reports which of the three network failures happened and the raw exception class
behind it, for example:

```
Connection refused -> The PC is on the network but LocalFlow is not running on it

kind=REFUSED  exception=ConnectException
```

That first line is the fix; the second is what to paste into a bug report.

## The four (five) permissions

Each row on the setup screen has an **Open** button that jumps to the right system page. Flip
the switch there, then come back; the row turns into a tick.

**Do them in this order: Microphone first, then Display over other apps, then the
Accessibility service, and only then flip Show the dot.** The order matters on Android 14 and
newer: the dot runs as a microphone foreground service, and Android refuses to start one until
the microphone permission has been granted. If you flip the switch before granting the
microphone, the app asks for it right there and starts the dot as soon as you tap *Allow*; if
you deny, the switch goes back off and a toast says why.

| Permission | Why the dot needs it | Stock Android | Samsung One UI |
|---|---|---|---|
| **Microphone** (grant first) | Records your voice. Asked when you flip *Show the dot* if not already granted. | Prompt → *While using the app*; or Settings → Apps → LocalFlow → Permissions → Microphone → *Allow only while using the app* | Same path |
| **Display over other apps** | Draws the dot on top of every other app. Without it the dot cannot appear at all. | Settings → Apps → Special app access → Display over other apps → LocalFlow → on | Settings → Apps → *⋮* → Special access → Appear on top → LocalFlow → on |
| **Accessibility service** | Types the text into whichever field is focused, at the cursor. Without it the text only goes to the clipboard. | Settings → Accessibility → (Downloaded / Installed apps) → LocalFlow → on → Allow | Settings → Accessibility → Installed apps → LocalFlow → on → Allow |
| **Notifications** (Android 13+) | The dot runs as a small always-on service; Android requires it to show a quiet notification (which has a *Stop* button). | Prompt; or Settings → Apps → LocalFlow → Notifications → on | Same path |
| **Battery: unrestricted** | Stops Android (Samsung especially) from killing the dot after a few minutes in the background. | Prompt "Let app always run in background" → Allow; or Settings → Apps → LocalFlow → Battery → *Unrestricted* | Settings → Apps → LocalFlow → Battery → *Unrestricted*; also check Settings → Battery → Background usage limits → make sure LocalFlow is not in *Sleeping apps* |

The accessibility page shows a warning about full control. That is Android's generic text for
every accessibility service. LocalFlow reads only the focused text field to insert text and
never sends anything anywhere except to your own PC.

Finally, flip **Show the dot**. The dot appears at the right edge of the screen the next time
a text field is active (tap into any field, or open the app's own Server URL field, to see it;
see *When it appears* below).

If you later revoke the microphone (or Android auto-resets unused permissions), the dot still
appears and its notification still works, but holding or tapping it opens the app with a
"needs the microphone" toast instead of recording. Grant it again and the next hold works
without restarting the dot.

## Using the dot

The dot works exactly like the desktop Flow Dot and shows the same colours.

**When it appears.** Like Wispr Flow, the dot only shows itself when you can type: it appears
as soon as a text field is focused or the keyboard comes up, and hides again about a third of a
second after you leave (so it does not blink when you hop between fields). It never disappears
while you are recording, while the PC is thinking, or during the green "inserted" flash. The
accessibility service is what tells the app that a field is active, so **with that service off
the dot stays visible on every screen**. To have it always visible anyway, switch off *Show the
dot only when a text field is active* in the app; the change applies immediately.

| Do this | What happens |
|---|---|
| **Hold** the dot, talk, **release** | Push-to-talk. The dot becomes a pill `✕ ▁▃▅▃▁ ✓` with a live waveform. On release it turns blue, the PC cleans your phrase, and the text appears at your cursor. Slide your finger onto **✕** before releasing to throw the recording away. |
| **Tap** the dot | Hands-free. Orange with a ring; talk as long as you like (up to 20 minutes). Tap the dot or **✓** when done, **✕** to discard. Cleaned as one speech, like the desktop double-tap mode. |
| **Drag** | Move it. It snaps to the nearest edge and remembers where. |
| Say "**press enter**" at the end | The PC strips the words and, if the switch in the app is on (default on), the phone presses Enter for you (sends the message, submits the search). |

| Dot | Meaning |
|---|---|
| Grey | Ready |
| Red, swelling with your voice | Recording (push-to-talk) |
| Orange with a ring, pulsing | Hands-free is on |
| Blue, pulsing | The PC is transcribing and cleaning |
| Green flash | Text inserted |
| Red ring + a toast saying why | Something went wrong. It clears after a couple of seconds — **tap the ring to see the message again** |
| Red ring that **stays** | …unless the PC timed out. Your audio is kept: **tap** the dot to send it again, or hold to start over. |

Every state change gives a small haptic tick (switch it off in the app). Sounds are off by
default.

**First dictation after a pause.** The PC unloads its cleanup model after about 10 idle minutes
to free the GPU, so the first dictation after a pause can take a few extra seconds while it
reloads. The app now pre-warms the PC when you tap into a text field and again the moment you
start recording, so you will rarely notice. If it still happens and the ring turns red, hold to
talk again while the ring is red and it retries.

Between two hands-free results the phone inserts a single space unless the text already ends in
a space or a newline. After push-to-talk it never adds trailing whitespace, so you can keep typing.

To stop the dot: the notification's **Stop** button, or the switch in the app.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The switch is on but there is no dot | *Display over other apps* is off | Grant it (row 2), then toggle the switch again |
| The dot never appears (or only on some screens) | *Show the dot only when a text field is active* is on and no text field is focused; or the accessibility service is off, in which case the app cannot tell and keeps the dot visible everywhere | Tap into a text field or open the keyboard and the dot appears. To see it all the time, switch that setting off. If it is on but the dot still shows everywhere, turn on the accessibility service (row 3). |
| Holding the dot shows a red ring and opens the app | No PC address or token is set yet | Paste the setup line (see *Connect it to your PC*); the highlighted card is where it goes |
| The switch flips itself back off | The microphone permission was denied (Android 14+ will not start the dot's service without it) | Grant Microphone (row 1; after two denials only the system page can, tap *Open*), then flip the switch again |
| Toast "Android refused microphone access for the dot" | Android 14+ blocked the upgrade to a microphone service while the app was in the background | Open the app once so it is in the foreground, then hold the dot again; check Battery is *Unrestricted* (row 5) |
| Text ends up on the clipboard, toast says "Copied, paste it where you want" | The accessibility service is off, or this app's text field refuses programmatic text (some browsers, some games) | Turn on the service (row 3). If it is on and one specific app still does this, that app blocks it: long-press → Paste. Please report which app. |
| Toast "Tailscale is off on this phone, or the PC name is wrong" | The PC's name did not resolve at all (`UnknownHostException`) | Open Tailscale on the phone and check it is connected; if it is, re-paste the setup line from the PC (tray → **Phone setup**) in case the PC was renamed |
| Toast "The PC is on the network but LocalFlow is not running on it" | The PC answered and refused the connection (`ECONNREFUSED`): it is awake and on the tailnet, but nothing is listening on the port | Start LocalFlow on the PC and check its menu shows *Enable phone access* ticked. If this keeps happening after you sign out of Windows, turn on **Start with Windows** in the PC's menu — signing out closes every app |
| Toast "The PC is offline or asleep" | Nothing answered before the connect timeout, or there is no route | Wake the PC; check Tailscale **on the PC**; check the PC shows as online in your tailnet |
| Toast "Wrong token" (401) | The token in the app does not match the PC | Copy the setup line again (the PC regenerates the token if you turn phone access off and on) |
| Toast "PC is warming up / paused" (503) | The PC's models are still loading, or you clicked *Pause (free GPU)* on the PC | Wait a minute, or resume the engine from the PC tray |
| Red ring stays, toast "PC busy, tap to retry" | The PC took more than 30 s (push-to-talk) or 120 s (hands-free), usually because a game has the GPU | Tap the dot to resend the same audio |
| The dot disappears after a while in the background | Battery optimisation killed the service | Row 5: set battery to *Unrestricted*; on Samsung also remove LocalFlow from *Sleeping apps* |
| The dot is gone after a reboot | Android does not let a microphone service start itself at boot | Open the app once; the dot comes back (the switch remembers) |
| Nothing is typed in a password field | Android hides password fields from accessibility services on purpose | Type those by hand |
| The text lands in the wrong place | The field lost focus while you were talking | Tap into the field first, then dictate; the text goes to wherever the cursor is when the reply arrives |

## What to report

Open an issue at <https://github.com/DatafyingTech/LocalFlow/issues> with:

- Phone model and Android version (Settings → About phone), and whether it is Samsung One UI.
- What you did (hold / tap / ✓ / ✕), what the dot showed, and the exact toast text.
- The app you were dictating into (the PC's `history.jsonl` also records it as `app`).
- Whether **Test connection** in the app succeeds, and what **Diagnose** prints (it names the
  failure and the exception class).
- The PC's `localflow.log` lines from around that time, if the request reached it.
- For text-insertion problems: what ended up in the field versus what you expected, and whether
  the text went to the clipboard instead.

## Building it yourself

```
cd android
./gradlew assembleDebug          # debug-key APK at app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease        # release APK at app/build/outputs/apk/release/app-release.apk
./gradlew testDebugUnitTest      # WAV header, text-splice, dot-visibility and URL tests, no device needed
```

`assembleRelease` signs with the permanent key only when `ANDROID_KEYSTORE_FILE`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` and `ANDROID_KEY_PASSWORD` are set (as
environment variables or Gradle `-P` properties); otherwise Gradle prints a warning and the
release APK comes out unsigned, which is fine for local development but will not install. The
keystore is not in the repository; CI gets it from repository secrets. Your own `assembleDebug`
build is signed with your machine's debug key and cannot be installed over a release build.

Needs JDK 17 and the Android SDK (platform 35; the build downloads build-tools 34.0.0 itself).
On Windows use `.\gradlew.bat`, make sure `JAVA_HOME` points at the JDK 17 folder, and check
`android/local.properties` has a plain `sdk.dir=C:/Users/you/AppData/Local/Android/Sdk` (forward
slashes, no `%VAR%`): a malformed `sdk.dir` fails with "The filename, directory name, or volume
label syntax is incorrect". GitHub Actions builds every push to `main` and every `android-v*`
tag (the tag build attaches the signed release APK to a GitHub release; `lintDebug` runs too,
warnings allowed).
The code is plain Kotlin views, one third-party dependency (OkHttp), and follows
[API.md](API.md) exactly: `POST /v1/dictate` with a 16 kHz mono WAV, `mode=ptt|handsfree`,
5 s connect / 30 s or 120 s read timeouts.
