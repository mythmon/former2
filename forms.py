from datetime import datetime

from flask import Flask, request
from flask.ext.sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config.from_pyfile('config.py')
db = SQLAlchemy(app)


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)
    form_name = db.Column(db.String(256))

    def __init__(self, form_name):
        self.form_name = form_name

    def __repr__(self):
        return '<Submission %s:%d>' % (self.form_name, self.id)


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


@app.route('/receiver/<form_name>', methods=['POST', 'GET'])
def receiver(form_name):
    submission = Submission(form_name)
    db.session.add(submission)
    for key, value in request.form.items():
        db.session.add(SubmissionRow(submission, key, value))
    db.session.commit()

    return str(submission)


if __name__ == '__main__':
    app.run()
