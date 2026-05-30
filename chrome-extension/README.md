# Labit arXiv Importer

This is the first Chrome extension path for importing arXiv PDFs into Labit.
It is intentionally small: the extension detects an arXiv abstract page, fetches
the PDF in the browser, and uploads metadata plus the PDF to the local Labit
FastAPI server.

## Load Locally

1. Start the Labit API:

   ```bash
   cd /home/qinglinh/Research-OS/vault/projects/Labit/code
   labit api --port 8787
   ```

2. Open Chrome:

   ```text
   chrome://extensions
   ```

3. Enable Developer mode.
4. Click "Load unpacked".
5. Select:

   ```text
   /home/qinglinh/Research-OS/vault/projects/Labit/code/chrome-extension
   ```

6. Open an arXiv abstract page, for example:

   ```text
   https://arxiv.org/abs/2401.12345
   ```

7. Open the Labit extension popup and click "Add to Labit".

When Labit runs on a VPS, keep the API behind SSH tunneling and set the popup
API field to the local forwarded URL:

```bash
ssh -L 8787:127.0.0.1:8787 qinglinh@209.38.142.55
```

Then use:

```text
http://127.0.0.1:8787
```
