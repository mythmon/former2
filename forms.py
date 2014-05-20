import os
import random
import subprocess
import traceback
from collections import OrderedDict
from datetime import datetime
from email.mime.text import MIMEText

import pytz
from flask import (Flask, request, redirect, abort, g, render_template,
                   url_for, send_from_directory)
from flask.ext.sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename


app = Flask(__name__)
# Defaults
app.config.update({
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///database.sqlite',
    'UPLOAD_FOLDER': 'uploads',
    'STATIC_FOLDER': 'static',
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

    def url_for(self, _external=False):
        return url_for('.viewer', form_name=self.form_name,
                       submission_id=self.id, _external=_external)


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

    def url_for(self, _external=False):
        return url_for('uploaded_file', filename=self.filename,
                       _external=_external)


def allowed_upload(filename):
    if '.' not in filename:
        return False
    _, ext = filename.rsplit('.', 1)
    ext = ext.lower()
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
        traceback.print_tb(error.__traceback__)
        g.after_request_callbacks = []
        return
    for (cb, args, kwargs) in getattr(g, 'after_response_callbacks', []):
        try:
            cb(*args, **kwargs)
        except Exception as e:
            print('Error calling', cb)
            print('{}: {}'.format(type(e).__name__, e))
            traceback.print_tb(e.__traceback__)


@app.template_filter('as_tz')
def as_tz(dt, tz_name):
    tz = pytz.timezone(tz_name)
    return pytz.utc.localize(dt).astimezone(tz)


@app.template_filter('fmtdatetime')
def fmtdatetime(dt):
    return dt.strftime('%m/%d/%Y %I:%M:%S %p')


def send_email_task(submission):
    form_meta = app.config['FORMS'].get(submission.form_name, {})
    pretty_name = form_meta.get('display_name', submission.form_name)
    to_addr = form_meta.get('email_to', app.config.get('EMAIL_DEFAULT_TO'))
    from_addr = form_meta.get('email_from',
                              app.config.get('EMAIL_DEFAULT_FROM'))

    try:
        context = {
            'url': submission.url_for(_external=True),
        }
        for row in submission.rows:
            context[row.key] = row.value
        print('context', context)
        message_text = render_template(submission.form_name + '.txt',
                                       **context)
    except flask.TemplateNotFound:
        message_text = (
            'There has been a new submission to {0}.\n\n'
            'You can see the submission at {1}\n'
            .format(pretty_name, submission.url_for(_external=True)))

    subject, message_text = message_text.split('\n\n', 1)

    if to_addr and from_addr:
        print('Sending email.')
        msg = MIMEText(message_text)
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Subject'] = subject

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

    for key, values in request.form.lists():
        for value in values:
            db.session.add(SubmissionRow(submission, key, value))

    for key, values in request.files.lists():
        for uploaded_file in values:
            if uploaded_file.filename == '':
                continue
            file = SubmissionFile.from_upload(submission, uploaded_file)
            db.session.add(file)

    db.session.commit()

    after_response(send_email_task, submission)

    form_meta = app.config['FORMS'].get(form_name, {})
    if 'redirect' in form_meta:
        return redirect(form_meta['redirect'])

    return 'ok'


@app.route('/viewer/<form_name>/<submission_id>')
def viewer(form_name, submission_id):
    submission = Submission.query.get_or_404(submission_id)
    form_meta = app.config['FORMS'].get(form_name, {})
    field_map = OrderedDict(form_meta.get('field_map', []))

    pretty_data = []

    for row in submission.rows:
        pretty_name = field_map.get(row.key, row.key)
        pretty_data.append((pretty_name, row.value))

    pretty_indexes = dict(zip(field_map.values(), range(len(field_map))))
    pretty_data.sort(key=lambda k: pretty_indexes.get(k[0], len(field_map)))

    context = {
        'form_name': form_name,
        'submission': submission,
        'pretty_data': pretty_data,
    }
    return render_template('viewer.html', **context)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/static/<filename>')
def static_file(filename):
    response = send_from_directory(app.config['STATIC_FOLDER'], filename)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


class ScriptNameStripper(object):
    to_strip = '/index.fcgi'

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path_info = environ.get('SCRIPT_NAME', '')
        environ['SCRIPT_NAME'] = path_info.replace(self.to_strip, '')
        return self.app(environ, start_response)


if app.config.get('FCGI'):
    app.wsgi_app = ScriptNameStripper(app.wsgi_app)


if __name__ == '__main__':
    app.run()
