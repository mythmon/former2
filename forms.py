import os
import random
import subprocess
import traceback
from datetime import datetime
from email.mime.text import MIMEText

from flask import Flask, request, redirect, abort, g
from flask.ext.sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename


app = Flask(__name__)
# Defaults
app.config.update({
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///database.sqlite',
    'UPLOAD_FOLDER': 'uploads',
    'UPLOAD_ALLOWED_EXTENSIONS': ['png', 'gif', 'jpg', 'bmp'],
    'FORMS': {},
    'EMAIL_DEFAULT_TO': None,
    'EMAIL_DEFAULT_FROM': None,
})
app.config.from_pyfile('config.py')

db = SQLAlchemy(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)
    form_name = db.Column(db.String(256))

    def __init__(self, form_name):
        self.form_name = form_name

    def __repr__(self):
        return '<Submission %s:%d>' % (self.form_name, self.id)

    @property
    def url(self):
        return 'http://example.org/submission/{0}'.format(self.id)


class SubmissionRow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(256))
    value = db.Column(db.Text)
    submission_id = db.Column(db.Integer, db.ForeignKey('submission.id'))
    submission = db.relationship('Submission', backref=db.backref('rows'))

    def __init__(self, submission, key, value):
        self.submission = submission
        self.key = key
        self.value = value

    def __repr__(self):
        return '<SubmissionRow %s=%s>' % (self.key, self.value)


class SubmissionFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(256))
    filename = db.Column(db.String(1024))
    original_filename = db.Column(db.String(1024))
    submission_id = db.Column(db.Integer, db.ForeignKey('submission.id'))
    submission = db.relationship('Submission', backref=db.backref('files'))

    def __init__(self, submission, key, filename, original_filename):
        self.submission = submission
        self.key = key
        self.filename = filename
        self.original_filename = original_filename

    def __repr__(self):
        return '<SubmissionFile %s=%s>' % (self.key, self.filename)

    @classmethod
    def from_upload(cls, submission, uploaded_file):
        if not allowed_upload(uploaded_file.filename):
            abort(400, 'File type not allowed')
        filename = get_safe_filename(uploaded_file.filename)
        uploaded_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return cls(submission, uploaded_file.name, filename,
                   original_filename=uploaded_file.filename)


def allowed_upload(filename):
    _, ext = filename.rsplit('.', 1)
    return '.' in filename and ext in app.config['UPLOAD_ALLOWED_EXTENSIONS']


def get_safe_filename(filename):
    filename = secure_filename(filename)
    while True:
        rand = ''.join(random.choice('abcdef0123456789') for _ in range(16))
        safe = rand + '_' + filename
        full_filename = os.path.join(app.config['UPLOAD_FOLDER'], safe)
        if not os.path.exists(full_filename):
            return safe


def after_response(func, *args, **kwargs):
    print('queueing', func, args, kwargs)
    if not hasattr(g, 'after_response_callbacks'):
        g.after_response_callbacks = []
    g.after_response_callbacks.append((func, args, kwargs))


@app.teardown_request
def call_after_response_callbacks(error=None):
    print('running after_response callbacks')
    if error:
        print('Error passed:', error)
        g.after_request_callbacks = []
        return
    for (cb, args, kwargs) in getattr(g, 'after_response_callbacks', []):
        try:
            cb(*args, **kwargs)
        except Exception as e:
            print('Error calling', cb)
            print('{}: {}'.format(type(e).__name__, e))
            traceback.print_tb(e.__traceback__)


def send_email_task(submission):
    form_meta = app.config['FORMS'].get(submission.form_name, {})
    pretty_name = form_meta.get('display_name', submission.form_name)
    to_addr = form_meta.get('email_to', app.config.get('EMAIL_DEFAULT_TO'))
    from_addr = form_meta.get('email_from', app.config.get('EMAIL_DEFAULT_FROM'))

    if to_addr and from_addr:
        print('Sending email.')
        msg = MIMEText(submission.url)
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Subject'] = ('There has been a new submission to {0}.'
                          .format(pretty_name))

        p = subprocess.Popen(['/usr/sbin/sendmail', '-t', '-i'],
                             stdin=subprocess.PIPE)
        stdout, stderr = p.communicate(msg.as_string().encode())
        print('stdout', stdout)
        print('stderr', stderr)
    else:
        print('cannot send mail, to_addr:', to_addr, 'from_addr:', from_addr)


@app.route('/receiver/<form_name>', methods=['POST'])
def receiver(form_name):
    submission = Submission(form_name)
    db.session.add(submission)
    for key, value in request.form.items():
        db.session.add(SubmissionRow(submission, key, value))
    for uploaded_file in request.files.values():
        db.session.add(SubmissionFile.from_upload(submission, uploaded_file))
    db.session.commit()

    after_response(send_email_task, submission)

    return 'ok'


if __name__ == '__main__':
    app.run()
