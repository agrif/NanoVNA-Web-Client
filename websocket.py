#!/usr/bin/env python3

# adapted from
# https://gist.github.com/artizirk/04eb23d957d7916c01ca632bb27d5436

import os
import sys
import asyncio
import datetime
import random
import functools
import mimetypes
import contextlib
import threading
from http import HTTPStatus

import serial
import serial.threaded
import websockets

async def process_request(sever_root, path, request_headers):
	if 'Upgrade' in request_headers:
		if path.split('/')[-1] != 'nanovna-control':
			# not our websocket
			return HTTPStatus.NOT_FOUND, [], b'404 NOT FOUND'
		# it *is* our websocket
		return

	if path == '/':
		path = '/index.html'

	response_headers = [
		('Server', 'NanoVNA Web Client Websocket Server'),
		('Connection', 'close'),
	]

	# Derive full system path
	full_path = os.path.realpath(os.path.join(sever_root, path[1:]))

	# Validate the path
	if os.path.commonpath((sever_root, full_path)) != sever_root or \
			not os.path.exists(full_path) or not os.path.isfile(full_path):
		print('HTTP GET {} 404 NOT FOUND'.format(path))
		return HTTPStatus.NOT_FOUND, [], b'404 NOT FOUND'

	# Guess file content type
	extension = full_path.split('.')[-1]
	(mime_type, encoding_type) = mimetypes.guess_type(path)
	if mime_type is None:
		mime_type = 'application/octet-stream'
	response_headers.append(('Content-Type', mime_type))
	if encoding_type:
		response_headers.append(('Content-Encoding', encoding_type))

	# Read the whole file into memory and send it out
	body = open(full_path, 'rb').read()
	response_headers.append(('Content-Length', str(len(body))))
	print('HTTP GET {} 200 OK'.format(path))
	return HTTPStatus.OK, response_headers, body


async def process_websocket(inner, websocket, path):
	print('CONTROL OPEN ', websocket.remote_address)
	try:
		await inner(websocket)
	finally:
		print('CONTROL CLOSE ', websocket.remote_address)

class VnaProtocol(serial.threaded.Protocol):
	def __init__(self, ws):
		super().__init__()
		self.ws = ws

	def data_received(self, data):
		# web frontend expects binary data to be encoded as
		# unicode codepoints, and latin1 is the identity map
		msg = data.decode('latin1')
		#print('CONTROL <<< ', repr(msg))
		asyncio.run(self.ws.send(msg))

NANOVNA_THREAD_LOCK = threading.RLock()
NANOVNA_THREAD = None
@contextlib.contextmanager
def assume_control(nanovna_path, ws):
	global NANOVNA_THREAD
	global NANOVNA_THREAD_LOCK
	with NANOVNA_THREAD_LOCK:
		if NANOVNA_THREAD:
			# shouldn't need a lock here, becuase websockets
			# handler is asyncio in one thread
			NANOVNA_THREAD.close()
			NANOVNA_THREAD = None
		vna = serial.Serial(nanovna_path, 115200)
		thread = serial.threaded.ReaderThread(vna, lambda: VnaProtocol(ws))
		NANOVNA_THREAD = thread
		NANOVNA_THREAD.start()
	try:
		yield vna
	finally:
		with NANOVNA_THREAD_LOCK:
			if NANOVNA_THREAD is thread:
				NANOVNA_THREAD.close()
				NANOVNA_THREAD = None

async def nanovna_control(nanovna_path, ws):
	with assume_control(nanovna_path, ws) as vna:
		while ws.open and vna.is_open:
			msg = await ws.recv()
			#print('CONTROL >>> ', repr(msg))
			with NANOVNA_THREAD_LOCK:
				if vna.is_open:
					# web frontend expects binary data to be encoded as
					# unicode codepoints, and latin1 is the identity map
					vna.write(msg.encode('latin1'))

if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser()
	parser.add_argument('-b', '--bind', metavar='ADDRESS', help='bind to this address (default: all interfaces)')
	parser.add_argument('-d', '--directory', default=os.getcwd(), help='serve this directory (default: current directory)')
	parser.add_argument('vna', help='NanoVNA serial port')
	parser.add_argument('port', default=8000, type=int, nargs='?', help='bind to this port (default: %(default)s)')
	args = parser.parse_args()

	handler_r = functools.partial(process_request, args.directory)
	handler_nano = functools.partial(nanovna_control, args.vna)
	handler_ws = functools.partial(process_websocket, handler_nano)
	start_server = websockets.serve(handler_ws, args.bind, args.port, process_request=handler_r)
	print('Running server at http://{}:{}/'.format(args.bind if args.bind else '[::]', args.port))

	asyncio.get_event_loop().run_until_complete(start_server)
	asyncio.get_event_loop().run_forever()
