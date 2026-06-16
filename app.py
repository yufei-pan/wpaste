#!/usr/bin/env python3
from flask import Flask, request, jsonify, render_template, send_file, abort
from datetime import datetime, timedelta
import time
import os
import random
import json
import TSVZ
#import imghdr
import filetype

version = '1.5.0'

#TODO: add feature: copy from the webpage should be easier : ctrl c copy the last message , add a copy to clipboard button to messages
#TODO: add periodic update / event based update to the webpage

# print with flush on
from functools import partial
print = partial(print, flush=True)

# ---------------------------------------------------------------------------
# Configuration
#
# Defaults below can be overridden by a JSON config file. The first file found
# in CONFIG_SEARCH_PATHS wins. Sizes accept plain bytes or human-readable
# strings like "16GB"/"100MB"; durations accept seconds or strings like
# "4h"/"30m". Only keys present in DEFAULT_CONFIG are honored.
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    'BASE_DIR': 'messages/',                        # where uploaded files live
    'INDEX_FILE': 'mainIndex.tsv',                  # TSVZ-backed message index
    'INDEX_REWRITE_INTERVAL': 3600 * 20,            # TSVZ compaction interval (s)
    'RETENTION_SIZE': '100MB',                       # hard-delete files larger than this
    'RETENTION_TIME': '4h',                          # purge entries older than this
    'MAX_CONTENT_LENGTH': '16GB',                    # max accepted upload size
    'HOST': '127.0.0.1',                             # dev server bind host
    'PORT': 5000,                                    # dev server bind port
    'DEBUG': True,                                    # dev server debug mode
}

CONFIG_SEARCH_PATHS = [
    'wpaste.config.json',
    os.path.expanduser('~/.wpaste.config.json'),
    os.path.expanduser('~/.config/wpaste/wpaste.config.json'),
    '/etc/wpaste.config.json',
]

_SIZE_UNITS = {'B': 1, 'K': 1024, 'KB': 1024, 'M': 1024**2, 'MB': 1024**2,
               'G': 1024**3, 'GB': 1024**3, 'T': 1024**4, 'TB': 1024**4}
_TIME_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}

def parse_size(value):
    '''Accept an int/float of bytes or a string like "16GB"/"100MB"/"512K".'''
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().upper()
    for unit in sorted(_SIZE_UNITS, key=len, reverse=True):
        if s.endswith(unit):
            return int(float(s[:-len(unit)].strip()) * _SIZE_UNITS[unit])
    return int(float(s))

def parse_duration(value):
    '''Accept an int/float of seconds or a string like "4h"/"30m"/"7d".'''
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    for unit, mult in _TIME_UNITS.items():
        if s.endswith(unit):
            return int(float(s[:-1].strip()) * mult)
    return int(float(s))

def load_config():
    config = dict(DEFAULT_CONFIG)
    for path in CONFIG_SEARCH_PATHS:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    user_config = json.load(f)
                unknown = set(user_config) - set(DEFAULT_CONFIG)
                if unknown:
                    print(f"Ignoring unknown config keys in {path}: {sorted(unknown)}")
                config.update({k: v for k, v in user_config.items() if k in DEFAULT_CONFIG})
                print(f"Loaded config from {path}")
            except (json.JSONDecodeError, OSError) as e:
                print(f"Failed to load config from {path}: {e}")
            break
    return config

_config = load_config()
BASE_DIR = _config['BASE_DIR']
INDEX_FILE = _config['INDEX_FILE']
INDEX_REWRITE_INTERVAL = int(_config['INDEX_REWRITE_INTERVAL'])
RETENTION_SIZE = parse_size(_config['RETENTION_SIZE'])  # hard-delete files bigger than this
RETENTION_TIME = parse_duration(_config['RETENTION_TIME'])  # purge entries older than this
MAX_CONTENT_LENGTH = parse_size(_config['MAX_CONTENT_LENGTH'])

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

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
    #format = imghdr.what(None, header)
    # imghdr had been deprecated, use filetype instead
    # if not format:
    #     return None
    # return '.' + (format if format != 'jpeg' else 'jpg')
    kind = filetype.guess(header)
    if kind is None:
        return None
    if kind.mime.startswith('image/'):
        return kind.extension
    return None

def __delete_file(message_id):
    global mainIndex
    global RETENTION_SIZE
    if message_id not in mainIndex:
        print(f"Message {message_id} not found in index.")
        return
    # rename file as <orginal_name>.deleted
    old_file_path = mainIndex[message_id][2]
    new_file_path = f"{old_file_path}.deleted"
    if os.path.exists(old_file_path):
        # check if it is being used currently
        # if the file is bigger than retension size, delete it
        if os.path.getsize(old_file_path) > RETENTION_SIZE:
            os.remove(old_file_path)
        else:
            os.rename(old_file_path, new_file_path)
    else:
        print(f"File not found: {old_file_path}")
    print(f"Message {message_id} deleted successfully.")

mainIndex = TSVZ.TSVZed(INDEX_FILE,header = ['id','unix_time','path','type','filename'],rewrite_interval=INDEX_REWRITE_INTERVAL,verbose=False)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')

@app.route('/message', methods=['POST'])
def post_message():
    message = ''
    if 'message' in request.form:
        message = request.form['message']
    today = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(BASE_DIR, today)

    if not os.path.exists(dir_path):
        os.makedirs(dir_path,exist_ok=True)
    
    if message.strip():
        file_id = generate_random_id()
        file_path = os.path.join(dir_path, f"{file_id}.txt")
        with open(file_path, 'w') as file:
            file.write(message)
        mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'text', f"{file_id}.txt"]
        update_last_modified()

    # Handle image upload - multiple files
    if 'image' in request.files:
        images = request.files.getlist('image')
        for image in images:
            if image.filename != '':
                file_id = generate_random_id()
                image_extension = validate_image(image.stream)
                if image_extension:
                    file_path = os.path.join(dir_path, f"{file_id}.{image_extension}")
                    image.save(file_path)
                    print(f"Image saved to {file_path}")
                    mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'image', image.filename]
                    update_last_modified()
                else:
                    return jsonify({"success": False, "message": f"Invalid image file: {image.filename}"})

    # Handle video upload - multiple files
    if 'video' in request.files:
        videos = request.files.getlist('video')
        for video in videos:
            if video.filename != '':
                file_id = generate_random_id()
                video_extension = os.path.splitext(video.filename)[1]
                if video_extension:
                    file_path = os.path.join(dir_path, f"{file_id}{video_extension}")
                    video.save(file_path)
                    print(f"Video saved to {file_path}")
                    mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'video', video.filename]
                    update_last_modified()
                else:
                    return jsonify({"success": False, "message": f"Invalid video file: {video.filename}"})
    
    # Handle general file upload - multiple files
    if 'file' in request.files:
        files = request.files.getlist('file')
        for file in files:
            if file.filename != '':
                file_id = generate_random_id()
                file_extension = os.path.splitext(file.filename)[1]
                file_path = os.path.join(dir_path, f"{file_id}{file_extension}")
                file.save(file_path)
                print(f"File saved to {file_path}")
                mainIndex[file_id] = [str(datetime.now().timestamp()), file_path, 'file', file.filename]
                update_last_modified()
                
    return jsonify({"success": True, "message": "Message saved successfully."})

@app.route('/last-update', methods=['GET'])
def get_last_update():
    return jsonify({"last_update": last_update_time})

@app.route('/messages', methods=['GET'])
def get_messages():
    messages = []
    message_to_delete = []
    now = datetime.now().timestamp()
    # Iterate over a snapshot of the keys so a concurrent POST/delete cannot
    # mutate the index mid-iteration; re-check membership before each access.
    for id in list(mainIndex):
        row = mainIndex[id] if id in mainIndex else None
        if row is None:
            continue
        unix_time, file_path, msg_type = float(row[1]), row[2], row[3]
        # Purge entries past their retention window.
        if now - unix_time > RETENTION_TIME:
            message_to_delete.append(id)
            continue
        # Reap orphaned entries whose backing file is gone instead of
        # surfacing them as permanent "Message not found." rows.
        if not os.path.exists(file_path):
            message_to_delete.append(id)
            continue
        if msg_type == 'image':
            content = f'/image/{id}'
        elif msg_type == 'text':
            with open(file_path, 'r') as file:
                content = file.read()
        elif msg_type == 'video':
            content = f'/video/{id}'
        elif msg_type == 'file':
            content = f'/file/{id}'
        else:
            content = "Content type not supported."
        messages.append({"id": id, "content": content, "timestamp": int(unix_time), "type": msg_type, "filename": row[4]})
    messages.reverse()
    for id in message_to_delete:
        delete_message(id)
    return jsonify({"messages": messages})

@app.route('/image/<message_id>', methods=['GET'])
@app.route('/video/<message_id>', methods=['GET'])
@app.route('/file/<message_id>', methods=['GET'])
def get_file(message_id):
    # remove the extension from the message_id if they included one
    message_id = os.path.splitext(message_id)[0]
    if message_id in mainIndex:
        file_path = mainIndex[message_id][2]
        # check if file_path is under BASE_DIR
        if os.path.commonpath([BASE_DIR, file_path]) != os.path.normpath(BASE_DIR):
            abort(404, description="Path not valid.")  # Return 404 error for invalid paths
        if os.path.exists(file_path):
            #return send_file(file_path)  # Directly send the file
            # try to the the mimetype
            mime = filetype.guess(file_path)
            if mime is not None:
                return send_file(file_path, mimetype=mime.mime,download_name=mainIndex[message_id][4])
            else:
                return send_file(file_path,download_name=mainIndex[message_id][4])
        else:
            abort(404, description="File not found.")  # Return 404 error for not found
    else:
        abort(404, description="Message not found.")  # Return 404 error for not found


@app.route('/delete_all', methods=['POST'])
def delete_all_messages():
    global mainIndex
    for id in list(mainIndex):
        __delete_file(id)
    mainIndex.clear()
    update_last_modified()  # Update last modified time after deletion
    return jsonify({"success": True, "message": "All messages have been deleted."})


@app.route('/delete/<message_id>', methods=['POST'])
def delete_message(message_id):
    if message_id in mainIndex:
        __delete_file(message_id)
        del mainIndex[message_id]
        update_last_modified()  # Update last modified time after deletion
        return jsonify({"success": True, "message": f"Message {message_id} deleted successfully."})
    return jsonify({"success": False, "message": "Message not found."})

if __name__ == '__main__':
    app.run(host=_config['HOST'], port=int(_config['PORT']), debug=bool(_config['DEBUG']))
