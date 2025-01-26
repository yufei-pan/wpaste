A simple web paste application.

install via

```bash
pip install -r requirements.txt
flask run
```

If you want a more production server,
I use gunicorn:
```bash
pip install gunicorn
gunicorn app:app
```

![screenshot1](/etc/Screenshot 2024-05-01 145831.png)

Currently support:

Text, HTML, and Imgaes
![screenshot2](/etc/Screenshot 2024-05-01 145853.png)
![screenshot3](/etc/Screenshot 2024-05-01 150454.png)

Drag and Drop

Ctrl C (CMD C)

Ctrl V (CMD V)

Select file

Delete messages manually

Delete ALL

TODO: ADD user session support to allow private copy paste boards.


Include TSVZ from https://github.com/yufei-pan/TSVZ
