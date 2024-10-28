import io
import os
import uuid
from flask import Flask, request, jsonify, send_file, after_this_request
from flask_cors import CORS
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.utils import secure_filename

# create the flask app instance (instance name must be 'application' for deploying to beanstalk)
application = Flask(__name__) 

CORS(application)

def get_page_sizes(pdf_reader):
    page_sizes = []

    for page in pdf_reader.pages:
        temp_writer = PdfWriter()
        temp_writer.add_page(page)
        temp_buffer = io.BytesIO()
        temp_writer.write(temp_buffer)
        length = len(temp_buffer.getvalue())
        page_sizes.append(length)

    return page_sizes

def _split_pdf(file_path, part_size_mb, output_dir):
    """Split a PDF file into parts of a given size. 
    Save parts in the 'output_dir'.

    In case a single page is larger than the part size (I think it's going to be rare to happen, specially with +5MB limit),
    it will be saved as a single part. May ask the user yes/no to split it in half or something like that... to complex for now

    Args:
        file_path (str): path to the pdf file to split
        part_size_mb (int): size of each part in MB
        output_dir (str): path to the folder to save the split pdf files

    Returns:
        bool: True if the pdf was split successfully, False otherwise   

    """

    # create a PdfReader object
    pdf_reader = PdfReader(file_path)
    total_pages = len(pdf_reader.pages)
    total_pages_sum_after = 0

    page_sizes = get_page_sizes(pdf_reader) # get the size of each page in the pdf

    part_size_bytes = part_size_mb * 1024 * 1024  # Convert MB to bytes
    current_part = 1
    current_writer = PdfWriter()
    accumulated_size = 0

    for i, page in enumerate(pdf_reader.pages):
        page_size = page_sizes[i]
        accumulated_size += page_size # "simulate" adding the page to the current part

        if i == 0: # very first page must be added (even if it's larger than the part size)
            current_writer.add_page(page)
            continue

        if accumulated_size >= part_size_bytes:
            # adding the current page will exeed the size limit, then save the current part (WITHOUT the current page)
            base_filename = os.path.splitext(os.path.basename(file_path))[0] # get the file name without the extension (.pdf)
            output_filename = f"{base_filename}_part_{current_part}.pdf"
            output_path = os.path.join(output_dir, output_filename) # path to save the split pdf file (output/unique_id/filename_part_X.pdf)

            part_length = len(current_writer.pages)

            with open(output_path, "wb") as output_file:
                current_writer.write(output_file) # write to file
                total_pages_sum_after += part_length

            print(f"Saved part #{current_part} with [{part_length}] pages")

            # Start a new part
            current_part += 1
            current_writer = PdfWriter()
            current_writer.add_page(page)
            accumulated_size = page_size

        else:
            current_writer.add_page(page) # add page for real

    # save last part...
    if len(current_writer.pages) > 0:
        base_filename = os.path.splitext(os.path.basename(file_path))[0] # file name without the extension
        output_filename = f"{base_filename}_part_{current_part}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        part_length = len(current_writer.pages)

        with open(output_path, "wb") as output_file:
            current_writer.write(output_file)
            total_pages_sum_after += part_length

        print(f"Saved part #{current_part} with [{part_length}] pages")

    print(f"PDF split into {current_part} parts.")
    print(f"Original file contains {total_pages} pages. Sum of pages after split: {total_pages_sum_after}")

    return total_pages == total_pages_sum_after

# Split pdf end point
@application.route('/api/split', methods=['POST'])
def split_pdf():
    """Get 'pdf_file' and'max_size' from the request and split the pdf file"""

    # get info from the request
    pdf_file = request.files['pdf_file']
    max_size = request.form['max_size']

    # do some validation...
    if not pdf_file or not max_size:
        return jsonify({'message': 'Pdf file and max_size are required'}), 400
    
    # max_size must be an int!
    try:
        max_size = int(max_size)
    except ValueError:
        return jsonify({'message': 'max_size must be an integer'}), 400
    
    # save uploaded file to a folder on the server
    # os.makedirs(application.config['UPLOAD_FOLDER'], exist_ok=True)
    # file_name = secure_filename(pdf_file.filename) # secure the file name so that it's a safe file name (no malicious code)
    # file_path = os.path.join(application.config['UPLOAD_FOLDER'], file_name) # using os.path.join to join the folder and the file name (cross-platform)
    
    # save the file to the server on a temporary folder (prevent race condition)
    unique_id = str(uuid.uuid4())
    upload_dir = os.path.join('', unique_id + '_upload') # folder to keep the uploaded pdf file
    output_dir = os.path.join('', unique_id) # folder to keep the split pdf files

    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    file_path_on_server = os.path.join(upload_dir, secure_filename(pdf_file.filename))
    pdf_file.save(file_path_on_server) # save the file to the server, at 'unique_id/file_name.pdf'

    # help(pdf_file) // used to see the methods and props available on the file object

    # split the pdf file
    try:
        if _split_pdf(file_path_on_server, max_size, output_dir):
            # zip the output folder and send it to the client
            zip_file_name = unique_id + '_pdfs.zip' # name of the zip file to be downloaded
            
            # zip the 'folder_to_zip' and save it as 'zip_file_name'. 
            # run zip command according to the OS
            if os.name == 'nt':
                os.system(f"tar -a -c -f {zip_file_name} {output_dir}") # windows
            else:
                os.system(f"zip -r {zip_file_name} {output_dir}") # linux/mac

            # remove the temporary folders (shi not working for deleting the file...)
            @after_this_request
            def remove_file(response):
                try:
                    print('Removing temporary files...')
                    os.system(f"rm -rf {upload_dir}")
                    os.system(f"rm -rf {output_dir}")
                    os.system(f"rm -rf *.zip")
                    os.system(f"ls")
                except Exception as e:
                    print(f"An error occurred while removing things: {e}")
                return response

            return send_file(zip_file_name, as_attachment=True)
        else:
            raise Exception("Failed to split the pdf file. Number of pages don't match.")

    except Exception as e:
        return jsonify({'message': str(e)}), 500

@application.route('/tmp', methods=['GET'])
def single_text():
    res = {
        'message': 'Message from the server',
        'another_key': 'another_value'
    }

    return jsonify(res)

if __name__ == '__main__':
    application.run(debug=True)