# Google Drive Hash Audit

A small Python command-line project that reads file metadata from Google Drive and exports the checksums that Google Drive makes available.

It is useful when you want to create a Google Drive inventory and later compare it against files stored on an external hard drive or other local backup.

## What it does

- Authenticates to Google Drive using OAuth 2.0.
- Reads all non-trashed items visible to the authenticated account.
- Uses pagination, so it works with Drives containing more than 1,000 files.
- Reconstructs a best-effort path from the parent metadata returned by Drive.
- Retrieves available `md5Checksum`, `sha1Checksum`, and `sha256Checksum` fields.
- Exports results to CSV or JSON.
- Does **not** download the file contents.
- Uses the read-only Drive metadata OAuth scope.

## Important checksum behavior

Google Drive checksum fields are metadata supplied by Google. They are normally applicable to binary files stored in Drive, such as ZIP files, PDFs, images, videos, and other uploaded files.

Google-native files such as Google Docs, Sheets, and Slides do not behave like ordinary uploaded binary files, so checksum fields can be empty. Folders also do not have content hashes.

Do not assume that every Drive file will contain all three checksum fields. The program exports whatever the Drive API returns.

## Requirements

- Python 3.10 or newer recommended
- A Google account
- A Google Cloud project with the Google Drive API enabled
- OAuth Desktop Application credentials

## Project structure

```text
gdrive-hash-audit/
├── gdrive_hash_audit/
│   ├── __init__.py
│   └── main.py
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

## 1. Create Google API credentials

Open Google Cloud Console and create or select a project.

1. Enable **Google Drive API**.
2. Configure the OAuth consent screen if required.
3. Create an OAuth Client ID.
4. Select **Desktop app** as the application type.
5. Download the JSON credentials file.
6. Rename it to:

```text
credentials.json
```

7. Put it in the project root.

Do **not** commit `credentials.json` to GitHub. It is already excluded by `.gitignore`.

## 2. Create a virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies:

- `google-api-python-client`
- `google-auth`
- `google-auth-oauthlib`

## 4. Run the inventory

From the project root:

```bash
python -m gdrive_hash_audit.main
```

The first run opens a browser window and asks you to sign in to Google and grant read-only metadata access.

After authorization, a local file named `token.json` is created. It stores the OAuth token so you usually do not need to authenticate on every run.

`token.json` is also excluded by `.gitignore` and should not be committed.

## Default output

The command creates:

```text
gdrive_hashes.csv
```

Example:

```csv
path,name,id,mimeType,size,md5Checksum,sha1Checksum,sha256Checksum,createdTime,modifiedTime
Movies/movie1.mkv,movie1.mkv,1ABC...,video/x-matroska,4819234812,70f1...,db75...,3aae...,2026-01-01T10:00:00.000Z,2026-01-01T10:00:00.000Z
Photos/photo.jpg,photo.jpg,1XYZ...,image/jpeg,3812921,3ba9...,91ce...,d1a8...,2026-02-10T08:00:00.000Z,2026-02-10T08:00:00.000Z
```

The actual checksum fields available depend on what Google Drive returns for each file.

## JSON output

```bash
python -m gdrive_hash_audit.main \
  --format json \
  --output gdrive_hashes.json
```

## Include folders

Folders are excluded from the output by default because they have no file-content checksum.

To include them:

```bash
python -m gdrive_hash_audit.main --include-folders
```

## Custom credentials/token locations

```bash
python -m gdrive_hash_audit.main \
  --credentials /path/to/credentials.json \
  --token /path/to/token.json
```

## Command-line options

```bash
python -m gdrive_hash_audit.main --help
```

Options:

```text
--credentials PATH     OAuth client credentials JSON
--token PATH           Cached OAuth token
--output PATH          Output inventory filename
--format csv|json      Output format
--include-folders      Include Drive folders in inventory
```

## Security

The application requests this OAuth scope:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
```

This allows the program to inspect Drive file metadata without requesting permission to modify or delete your Drive files.

Never commit these files:

```text
credentials.json
token.json
```

## Comparing against local files

The generated inventory is intended to become one side of a backup verification process.

For example:

```text
Google Drive                         External HDD
-----------------------------        -----------------------------
Movies/movie1.mkv                    data/Movies/movie1.mkv
SHA-256: abc123...             <=>   SHA-256: abc123...
                                      MATCH
```

A future/local comparison script can classify files as:

```text
MATCH
HASH MISMATCH
LOCAL ONLY
GOOGLE DRIVE ONLY
NO DRIVE CHECKSUM
```

When possible, compare using the same hash algorithm present on both sides. SHA-256 is preferred where Drive supplies it; MD5 remains useful because it is commonly available for binary Drive content.

## Notes about paths

Google Drive fundamentally identifies files and folders by IDs. The API returns parent IDs rather than a traditional filesystem path. This project reconstructs a convenient path from the parent metadata available in the inventory.

A Drive file can also have situations that do not map perfectly to a traditional filesystem hierarchy. For audit purposes, the Google Drive file ID remains the authoritative unique identifier.

## Push to GitHub

```bash
git init
git add .
git commit -m "Initial Google Drive hash audit tool"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/gdrive-hash-audit.git
git push -u origin main
```

Before pushing, verify that secrets are ignored:

```bash
git status
```

`credentials.json` and `token.json` should not appear as files to commit.

## License

MIT

## Troubleshooting

### Google OAuth: `Error 403: access_denied` / app has not completed Google verification

During the first OAuth sign-in, Google may show a message similar to:

```text
Access blocked: <app name> has not completed the Google verification process
The app is currently being tested and can only be accessed by developer-approved testers.
Error 403: access_denied
```

This normally means the OAuth application is still in **Testing** mode and the Google account you are signing in with has not been added as a test user. It is not a Python error and does not mean the Drive API code is broken.

To resolve it:

1. Open the Google Cloud Console and select the project used for `credentials.json`.
2. Open **Google Auth Platform** and then **Audience**.
3. Confirm the application is in **Testing** mode.
4. Under **Test users**, choose **Add users**.
5. Add the Google account that will be used to access Drive.
6. Save the change.
7. Delete any previously generated `token.json` so authentication starts again.
8. Run the program and sign in with the test-user account.

Linux/macOS:

```bash
rm -f token.json
python -m gdrive_hash_audit.main
```

Windows PowerShell:

```powershell
Remove-Item token.json -ErrorAction SilentlyContinue
python -m gdrive_hash_audit.main
```

For personal use or development, the application can remain in Testing mode with your Google account registered as a test user. If you publish this project on GitHub, do not publish your OAuth credentials for everyone to use. Each user should create their own Google Cloud project/OAuth Desktop client and supply their own `credentials.json`.

### Git for Windows: `fork: Resource temporarily unavailable`

While working with the repository on Windows, Git Bash may fail with messages such as:

```text
sh: fork: retry: Resource temporarily unavailable
git-submodule: fork: Resource temporarily unavailable
exit code 0xC0000142
```

This error comes from the Git for Windows/Git Bash environment rather than from `gdrive-hash-audit`.

Try the following:

1. Close all Git Bash, VS Code, IntelliJ, and other terminals using Git, then open a fresh terminal.
2. Verify Git itself works:

```bash
git --version
git status
```

3. Try the Git operation from **Windows PowerShell** instead of Git Bash:

```powershell
git status
git add .
git commit -m "Initial commit"
git push
```

4. Restart Windows if Git Bash continues to fail to create child processes.
5. If the problem remains after a restart, update or reinstall the latest Git for Windows.
6. Check antivirus/endpoint-security software if it is preventing Git's shell processes from starting.

You can see which Git installation PowerShell is using with:

```powershell
where.exe git
```

This project does not require Git submodules, so a `git-submodule` failure is unrelated to the Google Drive hashing functionality.

## GitHub credential safety

The repository's `.gitignore` excludes both OAuth files:

```text
credentials.json
token.json
```

Before every first push, it is still a good idea to verify:

```bash
git status
```

If either OAuth file was accidentally committed before it was added to `.gitignore`, simply adding it to `.gitignore` does not remove it from Git history. Remove it from Git tracking and rotate/revoke exposed credentials as appropriate before publishing the repository.
