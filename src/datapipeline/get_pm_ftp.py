import concurrent.futures
import ftplib
import hashlib
import os
from contextlib import closing

from argparse import ArgumentParser

parser = ArgumentParser()

parser.add_argument(
    "--start-index", help="Starting index for pubmed ftp file (range 1:1274) (Default = 1)", default=1, type=int
)

args = parser.parse_args()

# --- Configuration ---
FTP_HOST = "ftp.ncbi.nlm.nih.gov"
FTP_DIR = "/pubmed/baseline/"
LOCAL_DIR = "outputs/pubmed_baseline_ftp"
FILES_TO_DOWNLOAD = [f"pubmed25n{i:04d}.xml.gz" for i in range(args.start_index, 1275)]
MAX_WORKERS = min(8, os.cpu_count())


def compute_md5(filepath: str) -> str:
    hasher = hashlib.md5()
    with open(filepath, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fetch_expected_md5(ftp: ftplib.FTP, filename: str) -> str:
    md5_filename = f"{filename}.md5"
    lines: list[str] = []
    try:
        ftp.retrlines(f"RETR {md5_filename}", callback=lines.append)
    except ftplib.all_errors:
        return ""
    line = next((line for line in lines if line.strip()), "")
    if not line:
        return ""
    if "=" in line:
        _, _, remainder = line.partition("=")
        return remainder.strip()
    parts = line.split()
    return parts[-1].strip() if parts else ""


def download_file(filename):
    """Downloads a single file from the FTP server."""
    print(f"Starting download: {filename}")
    local_filepath = os.path.join(LOCAL_DIR, filename)

    try:
        with closing(ftplib.FTP(FTP_HOST)) as ftp:
            ftp.login(user="anonymous", passwd="anonymous@example.com")
            ftp.cwd(FTP_DIR)

            expected_md5 = fetch_expected_md5(ftp, filename)

            if os.path.exists(local_filepath):
                if expected_md5:
                    current_md5 = compute_md5(local_filepath)
                    if current_md5 == expected_md5:
                        print(f"Skipping {filename}; already present with matching checksum")
                        return f"SKIPPED: {filename}"
                    print(f"Existing file {filename} has mismatched checksum; re-downloading")
                    try:
                        os.remove(local_filepath)
                    except OSError:
                        pass
                else:
                    print(f"Skipping {filename}; already present and no checksum available")
                    return f"SKIPPED: {filename}"

            with open(local_filepath, "wb") as local_file:
                ftp.retrbinary(f"RETR {filename}", local_file.write)

        if not expected_md5:
            print(f"Warning: missing md5 checksum for {filename}; skipping verification")
            print(f"Finished download: {filename}")
            return f"SUCCESS: {filename}"

        actual_md5 = compute_md5(local_filepath)
        if actual_md5 == expected_md5:
            print(f"Finished download: {filename}")
            return f"SUCCESS: {filename}"

        print(f"MD5 mismatch detected for {filename} (expected {expected_md5}, got {actual_md5})")
        try:
            os.remove(local_filepath)
        except OSError:
            pass
        return f"FAILURE: {filename} (md5 mismatch)"

    except ftplib.all_errors as ftp_err:
        print(f"ERROR downloading {filename}: {ftp_err}")
        return f"FAILURE: {filename} ({ftp_err})"


# --- Main execution block ---
if __name__ == "__main__":
    # Create the local directory if it doesn't exist
    os.makedirs(LOCAL_DIR, exist_ok=True)
    print(f"Downloading {len(FILES_TO_DOWNLOAD)} files to {os.path.abspath(LOCAL_DIR)}")

    # Use ThreadPoolExecutor for concurrent downloads
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all download tasks
        future_to_file = {executor.submit(download_file, file): file for file in FILES_TO_DOWNLOAD}

        # Wait for all futures to complete and print results
        for future in concurrent.futures.as_completed(future_to_file):
            file = future_to_file[future]
            try:
                _ = future.result()
            except ftplib.all_errors as exc:
                print(f"{file} generated an FTP exception: {exc}")
            except (OSError, ValueError) as exc:
                print(f"{file} generated an error: {exc}")

    print("\nAll download tasks completed.")
