import glob
from argparse import ArgumentParser
import gzip
import shutil
import os


def extract_gz_file(input_filepath, output_filepath):
    """
    Decompresses a .gz file.

    Args:
        input_filepath (str): The path to the .gz file.
        output_filepath (str): The path for the decompressed file.
    """
    try:
        # Open the compressed file for reading in binary mode ('rb')
        with gzip.open(input_filepath, "rb") as f_in:
            # Open the output file for writing in binary mode ('wb')
            with open(output_filepath, "wb") as f_out:
                # Efficiently copy the contents (decompresses automatically)
                shutil.copyfileobj(f_in, f_out)

        print(f"Successfully decompressed '{input_filepath}' to '{output_filepath}'")

    except FileNotFoundError:
        print(f"Error: The file '{input_filepath}' was not found.")
    except Exception as e:
        print(f"An error occurred during extraction: {e}")


def main(arguments):
    # Define the output directory based on the input directory
    output_dir = arguments.xml_dir.rstrip(os.sep) + "_extract"

    # Create the output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    gz_files = glob.glob(os.path.join(arguments.xml_dir, "*.gz"))

    for file_path in gz_files:
        # Get the base filename (e.g., 'data.xml.gz')
        base_filename = os.path.basename(file_path)

        # Create the output filename by removing the '.gz' extension
        output_filename = (
            base_filename[:-3] if base_filename.lower().endswith(".gz") else base_filename + "_decompressed"
        )

        # Construct the full output path
        output_filepath = os.path.join(output_dir, output_filename)

        # Extract the file to the new location
        extract_gz_file(file_path, output_filepath)


if __name__ == "__main__":

    parser = ArgumentParser()

    parser.add_argument("--xml-dir", required=True, help="Directory containing .gz files to extract.")

    args = parser.parse_args()
    main(args)
