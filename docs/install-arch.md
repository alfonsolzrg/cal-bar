# Cal Bar and patched GNOME Calendar on Arch / CachyOS

This fork has two parts, and you can install either one on its own:

- **Cal Bar** on GNOME 51 with libical 4. Meetings are grouped by day, and each meeting shows a read-only guest list.
- **GNOME Calendar 51.0** with a patch so you can invite guests (suggested from your contacts) and add Google Meet calls in the event editor. It also adds a Join button, and the week view no longer jumps to the top after you delete an event.

Tested on CachyOS with GNOME 51, GTK 4.24, libadwaita 1.10, Evolution Data Server 3.62 and libical 4.

| Part | Fork branch | Patch |
|------|-------------|-------|
| Cal Bar | [alfonsolzrg/cal-bar `calbar`](https://github.com/alfonsolzrg/cal-bar/tree/calbar) | commit `02fea2c` on top of `romaad/cal-bar` |
| GNOME Calendar | [alfonsolzrg/gnome-calendar `calbar`](https://github.com/alfonsolzrg/gnome-calendar/tree/calbar) | [`packaging/arch/gnome-calendar/`](../packaging/arch/gnome-calendar/) `0001` + `0002` on top of tag `51.0` |

## 1. Install Cal Bar

```sh
sudo pacman -S --needed git python-gobject gtk4 libadwaita evolution-data-server \
  gnome-online-accounts gnome-control-center glib2 libcanberra sound-theme-freedesktop
git clone -b calbar https://github.com/alfonsolzrg/cal-bar.git
cd cal-bar
./install.sh
gnome-extensions enable cal-bar@local
```

On Wayland, log out and back in once so GNOME Shell loads the top-bar extension. To connect a calendar, run `cal-bar preferences` and click **Connect** (see the [README](../README.md#connect-and-select-calendars)).

To update later, run `git pull && ./install.sh`. A logout is only needed when the installer says the extension changed.

### Applying the Cal Bar patch to an upstream checkout instead

```sh
cd cal-bar                       # a clone of romaad/cal-bar
git fetch https://github.com/alfonsolzrg/cal-bar.git calbar
git cherry-pick 02fea2c          # or: curl -L https://github.com/alfonsolzrg/cal-bar/commit/02fea2c.patch | git am
./install.sh
```

The patch also applies cleanly to upstream `main` as of `5f4bb81`, and the unit tests pass there.

## 2. Install the patched GNOME Calendar (PKGBUILD)

The [PKGBUILD](../packaging/arch/gnome-calendar/PKGBUILD) is Arch's own `gnome-calendar` recipe with one more patch. It builds `gnome-calendar-51.0-106`, which replaces the system package. Its `pkgrel` is high so that a repository rebuild of 51.0 (`51.0-2`, `51.0-1.1`, …) doesn't overwrite it.

```sh
sudo pacman -S --needed base-devel git meson blueprint-compiler
cd cal-bar/packaging/arch/gnome-calendar
makepkg -Csi                     # -C: always build from a clean tree (see below)
pkill -x gnome-calendar          # restart it if it was running
```

A newer GNOME Calendar version (51.1 and up) would replace this build on the next `pacman -Syu`. To stop that, pin it in `/etc/pacman.conf`, adding it to any existing `IgnorePkg` line:

```ini
IgnorePkg = gnome-calendar
```

To go back to the stock package, remove that line and run `sudo pacman -S gnome-calendar`.

### Applying the GNOME Calendar patches by hand

```sh
git clone https://gitlab.gnome.org/GNOME/gnome-calendar.git
cd gnome-calendar
git checkout 51.0
git apply -3 ../cal-bar/packaging/arch/gnome-calendar/0001-Support-libical-4.0.0.patch  # Arch's libical 4 fix (upstream MR 763)
git apply -3 ../cal-bar/packaging/arch/gnome-calendar/0002-calbar-guests-and-meet.patch
meson setup build --prefix=/usr && meson compile -C build
```

Skip `0001` on a system that is still on libical 3. The result is the same as the `calbar` branch of the GNOME Calendar fork, which you can also clone directly.

## Updating the GNOME Calendar patch

After committing to the `calbar` branch of the GNOME Calendar fork, where `eba2fe2` is the libical 4 commit on top of `51.0`:

```sh
cd gnome-calendar
git diff eba2fe2 calbar > ../cal-bar/packaging/arch/gnome-calendar/0002-calbar-guests-and-meet.patch
cd ../cal-bar/packaging/arch/gnome-calendar
# bump pkgrel in PKGBUILD, then:
updpkgsums && makepkg -Csi
```

Always build clean (`-C`). Meson doesn't rebuild the compiled UI resources when a Blueprint file changes, so an incremental build silently ships the old UI.

## What's checked

- Cal Bar's unit tests pass on CachyOS with libical 4.
- GNOME Calendar's `/event/attendees/edit` and `test-utils` tests pass. `/event/date/create-tzid` fails, but it fails the same way on stock 51.0 with libical 4.
- Inviting guests and creating Meet calls is only tested against Google calendars added through GNOME Online Accounts.
