#!/usr/bin/env python3.11
from flask import Flask, request, jsonify, render_template, send_file, abort
from datetime import datetime, timedelta
import time
import os
import random
import json
import TSVZ
import imghdr

app = Flask(__name__)
BASE_DIR = './messages/'

version = '1.0.0'

#TODO: add feature: copy from the webpage should be easier : ctrl c copy the last message , add a copy to clipboard button to messages
#TODO: add periodic update / event based update to the webpage

if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)


# Global variable to store the last update time
last_update_time = time.time_ns()

# Function to update the global timestamp whenever messages are modified
def update_last_modified():
    global last_update_time
    last_update_time = time.time_ns()

def generate_random_id(length=8):
    letters = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz123456789'
    randStr = ''.join(random.choice(letters) for i in range(length))
    while randStr in mainIndex:
        randStr = ''.join(random.choice(letters) for i in range(length))
    return randStr

# Function to validate if file is an image
def validate_image(stream):
    header = stream.read(512)  # 512 bytes should be enough for a header check
    stream.seek(0)  # Reset stream pointer
    format = imghdr.what(None, header)
    if not format:
        return None
    return '.' + (format if format != 'jpeg' else 'jpg')


mainIndex = TSVZ.TSVZed('mainIndex.tsv',header = ['id','unix_time','path','type'],rewrite_interval=3600 * 20,verbose=False)

@app.route('/')
def index():
    return render_template('index.html')

# @app.route('/message', methods=['POST'])
# def post_message():
#     message = request.form['message']
#     today = datetime.now().strftime("%Y-%m-%d")
#     dir_path = os.path.join(BASE_DIR, today)
    
#     if not os.path.exists(dir_path):
#         os.makedirs(dir_path)
    
#     file_id = generate_random_id()
#     file_name = f"{file_id}.txt"
#     file_path = os.path.join(dir_path, file_name)
    
#     with open(file_path, 'w') as file:
#         file.write(message)
#     mainIndex[file_id] = [str(datetime.now().timestamp()),file_path]
    
#     return jsonify({"success": True, "message": "Message saved successfully."})
@app.route('/message', methods=['POST'])
def post_message():
    message = request.form['message']
    today = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(BASE_DIR, today)

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    
    if message.strip():
        file_id = generate_random_id()
        file_path = os.path.join(dir_path, f"{file_id}.txt")
        with open(file_path, 'w') as file:
            file.write(message)
        mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'text']

    # Handle image upload
    if 'image' in request.files:
        file_id = generate_random_id()
        image = request.files['image']
        if image.filename != '':
            image_extension = validate_image(image.stream)
            if image_extension:
                file_path = os.path.join(dir_path, f"{file_id}{image_extension}")
                image.save(file_path)
                mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'image']

    update_last_modified()  # Update last modified time
    return jsonify({"success": True, "message": "Message saved successfully."})

@app.route('/last-update', methods=['GET'])
def get_last_update():
    return jsonify({"last_update": last_update_time})

# {
#   "messages": [
#     {
#       "id": 1,
#       "content": "Welcome to our service!",
#       "timestamp": 1632096000
#     },
#     {
#       "id": 2,
#       "content": "Your appointment is confirmed for tomorrow.",
#       "timestamp": 1632182400
#     },
#     {
#       "id": 3,
#       "content": "System maintenance is scheduled for this weekend.",
#       "timestamp": 1632268800
#     }
#   ]
# }
@app.route('/messages', methods=['GET'])
def get_messages():
    messages = []
    for id in mainIndex:
        # if message is older than 7 days, mark it for deletion
        if datetime.now().timestamp() - float(mainIndex[id][1]) > 7 * 24 * 60 * 60:
            delete_message(id)
        else:
            if os.path.exists(mainIndex[id][2]):
                if mainIndex[id][3] == 'image':
                    content = f'/image/{id}'
                elif mainIndex[id][3] == 'text':
                    with open(mainIndex[id][2], 'r') as file:
                        content = file.read()
                else:
                    content = "Content type not supported."
            else:
                content = "Message not found."
            messages.append({"id": id, "content": content, "timestamp": int(float(mainIndex[id][1])), "type": mainIndex[id][3]})
    messages.reverse()
    return jsonify({"messages": messages})

@app.route('/image/<message_id>', methods=['GET'])
def get_image(message_id):
    if message_id in mainIndex:
        if mainIndex[message_id][3] == 'image':
            file_path = mainIndex[message_id][2]
            if os.path.exists(file_path):
                return send_file(file_path)  # Directly send the image file
            else:
                abort(404, description="Image not found.")  # Return 404 error for not found
        else:
            abort(400, description="Message is not an image.")  # Return 400 error for bad request
    else:
        abort(404, description="Message not found.")  # Return 404 error for not found


@app.route('/delete_all', methods=['POST'])
def delete_all_messages():
    for id in mainIndex:
        # rename all files as <orginal_name>.deleted
        old_file_path = mainIndex[id][2]
        new_file_path = f"{old_file_path}.deleted"
        os.rename(old_file_path, new_file_path)
    mainIndex.clear()
    update_last_modified()  # Update last modified time after deletion
    return jsonify({"success": True, "message": "All messages have been deleted."})


@app.route('/delete/<message_id>', methods=['POST'])
def delete_message(message_id):
    if message_id in mainIndex:
        # rename file as <orginal_name>.deleted
        old_file_path = mainIndex[message_id][2]
        new_file_path = f"{old_file_path}.deleted"
        if os.path.exists(old_file_path):
            os.rename(old_file_path, new_file_path)
        else:
            print(f"File not found: {old_file_path}")
        print(f"Message {mainIndex.pop(message_id)} deleted successfully.")
        update_last_modified()  # Update last modified time after deletion
        return jsonify({"success": True, "message": f"Message {message_id} deleted successfully."})
    return jsonify({"success": False, "message": "Message not found."})

if __name__ == '__main__':
    app.run(debug=True)
