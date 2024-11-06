#!/usr/bin/python3

from flask import Flask
from flask import request
from flask import make_response
from http import HTTPStatus

app = Flask(__name__)

@app.route('/<int:arg1>/<op>/<int:arg2>')
def calcGet(arg1, op, arg2):
    if not arg1 or not op or not arg2:
        return make_response('데이터 누락', HTTPStatus.BAD_REQUEST.value)

    if op == '+':
        result = arg1 + arg2
    elif op == '-':
        result = arg1 - arg2
    elif op == '*':
        result = arg1 * arg2
    else:
        return make_response('지원하지 않는 연산자', HTTPStatus.BAD_REQUEST.value)

    return make_response(f'{result}', HTTPStatus.OK)
    
@app.route('/', methods=['POST'])
def calcPost():
    arg1 = request.get_json().get('arg1')
    op = request.get_json().get('op')
    arg2 = request.get_json().get('arg2')
        
    if not arg1 or not op or not arg2:
        return make_response('데이터 누락', HTTPStatus.BAD_REQUEST)
    
    if op == '+':
        result = arg1 + arg2
    elif op == '-':
        result = arg1 - arg2
    elif op == '*':
        result = arg1 * arg2
    else:
        return make_response('지원하지 않는 연산자', HTTPStatus.BAD_REQUEST.value)

    return make_response(f'{result}', HTTPStatus.OK)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=20113)