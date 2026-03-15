# HAMLab Updater Release Flow

This guide explains exactly how to publish an update so installed HAMLab clients can detect and install it.

## What The App Checks

The updater in HAMLab reads the latest GitHub release from:

- Repository: `anshubhawsar/Hamlab_controller`
- API endpoint: `https://api.github.com/repos/anshubhawsar/Hamlab_controller/releases/latest`

A release is considered valid for auto-update only when:

1. The release is published (not draft).
2. The release has a newer tag than the app version (for example app `v2.4` and release `v2.4.1`).
3. The release contains an installer asset:
- `.exe` asset with `setup` or `installer` in the filename, or
- `.zip` asset with `setup` or `installer` in the filename (app extracts and runs installer `.exe` from zip).

## Standard Release Steps

1. Update app version in `hamlab_v2_3.py` (`APP_VERSION`).
2. Build artifacts:

```powershell
.\build_installer.bat
```

3. Verify artifacts are in `dist/installer`:
- `OneClick_Installer.exe`
- `OneClick_Installer.zip`

4. Commit and push source changes.
5. Create and push a version tag:

```powershell
git tag -a vX.Y.Z -m "vX.Y.Z release"
git push origin vX.Y.Z
```

6. Open GitHub Release page for that tag and publish release.
7. Upload one installer asset:
- Prefer `dist/installer/OneClick_Installer.exe`
- If uploader blocks `.exe`, upload `dist/installer/OneClick_Installer.zip`

8. Publish release (do not leave as Draft).

## Quick Verification After Publish

On an installed older version:

1. Open app.
2. Wait for update check in top bar.
3. Expected status: `Update: vX.Y.Z available`.
4. Click `Install Update` and complete installer.

## Troubleshooting

- Status: `No GitHub release published`
: Release is missing or still Draft.
- Status: `Up to date`
: Tag is not newer than installed version.
- Button disabled with release present
: Asset naming does not match expected installer pattern.

## Naming Rules (Recommended)

Use one of these names for release asset:


- `OneClick_Installer.zip`
- `HAMLab_Setup_vX.Y.Z.zip`

Keep asset in `dist/installer` as source of truth for releases.
