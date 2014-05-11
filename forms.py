from flask import Flask

app = Flask(__name__)

@app.route('/receiver/<form_name>', methods=['POST', 'GET'])
def receiver(form_name):
    return 'Hello World'

if __name__ == '__main__':
    app.run(host='0.0.0.0')
