# Task: Create new release v2.4.9 (manual PWM speed control + cooling fixes)

## Status: IN PROGRESS

## Plan Steps
- [x] Commit current PWM/cooling update and push to GitHub
- [x] Bump version to v2.4.9:
  - [x] `hamlab_v2_3.py` → `APP_VERSION = "v2.4.9"`
  - [x] `build_installer.bat` → `APP_VERSION=v2_4_9`
  - [x] `installer/HAMLAB_Setup.iss` → `MyAppVersion "2.4.9"`
  - [x] `create_release.py` → TAG v2.4.9 + release notes
  - [x] `publish_release.py` → TAG v2.4.9 + release notes
- [ ] Build new installer via `build_installer.bat`
- [ ] Commit & push version bump + built installer
- [ ] Create git tag `v2.4.9` and push it
- [ ] Create GitHub Release v2.4.9 and upload installer asset
