import socket
import sys
import threading
import json
import select
import os

from absl import app, flags

import message_pb2 as pb

FLAGS = flags.FLAGS

flags.DEFINE_string(name="ip", default="", help="서버 bind IP 주소")
flags.DEFINE_integer(
    name="port", default=None, required=True, help="서버 bind PORT 번호"
)
flags.DEFINE_integer(name="workers", default=2, help="worker thread 개수")
flags.DEFINE_enum(
    name="format", default="json", enum_values=["json", "protobuf"], help="메시지 포맷"
)

nextRoomId = 1  # 다음에 생성될 방 번호를 지정하는 변수. 방이 생성될 때마다 +1

inputs = []  # 멀티플렉싱 read 이벤트 체크할 목록
clientSocks = []  # 클라이언트 목록을 담는 리스트.
taskQueue = []  # 처리할 작업을 담는 리스트
rooms = {}  # 채팅방 목록을 담는 딕셔너리
members = []  # 멤버 목록을 담는 리스트
membersDict = {}  # socket descriptor : member 객체 맵핑해둔 dictionary

taskMutex = threading.Lock()  # task 관련 뮤텍스
roomMutex = threading.Lock()  # room 관련 뮤텍스
taskFilled = threading.Condition(taskMutex)  # condition variable
quit = threading.Event()  # 종료 신호를 위한 Event 객체

socketBuf = b""
currentMessageLen = None
currentProtobufType = None


class SocketClosed(RuntimeError):
    pass


class NoTypeFieldInMessage(RuntimeError):
    pass


class UnknownTypeInMessage(RuntimeError):
    def __self__(self, _type):
        self.type = _type

    def __str__(self):
        return str(self.type)


class member:
    def __init__(self, sock, name):
        self.sock = sock
        self.name = name
        self.room = None


def add_to_members(sock, name):
    """
    members 리스트, membersDict 딕셔너리에 새 member 정보를 추가한다
    """
    newMember = member(sock=sock, name=name)
    members.append(newMember)
    membersDict[sock] = newMember

    return newMember


def remove_from_members(sock):
    """
    members 리스트, membersDict 딕셔너리로부터 member 정보를 삭제한다
    """
    targetMember = find_member(sock)
    if targetMember in members:
        members.remove(targetMember)
    del membersDict[sock]


def find_member(sock):
    """
    membersDict 딕셔너리로부터 sock에 해당하는 member 객체를 찾는다
    """
    return membersDict.get(sock)


class room:
    def __init__(self, title):
        global nextRoomId
        self.roomId = nextRoomId
        self.title = title
        self.members = []
        nextRoomId += 1

    def add_member(self, member):
        """
        클라이언트가 채팅방에 참여
        """
        self.members.append(member)

    def remove_member(self, member):
        """
        클라이언트가 채팅방을 나갔을 때 사용
        """
        if member in self.members:
            self.members.remove(member)
        else:
            print("채팅방에 해당 멤버가 없음")


def on_name(sock, msg):
    """
    /name에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    :param msg: 메시지
    """

    # 해당하는 멤버 찾기
    targetMember = find_member(sock)
    if not targetMember:
        print("해당하는 멤버 없음")

    oldName = targetMember.name
    # newName = msg["name"]

    # 1. 현재 클라이언트에게 시스템 메시지 send
    messages = []
    if FLAGS.format == "json":
        newName = msg["name"]
        message = {
            "type": "SCSystemMessage",
            "text": f"이름이 {newName} 으로 변경되었습니다.",
        }
        messages.append(message)
    else:
        # protobuf
        newName = msg.name

        message = pb.Type()
        message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
        messages.append(message)

        message = pb.SCSystemMessage()
        message.text = f"이름이 {newName} 으로 변경되었습니다."
        messages.append(message)

    serializedMsgs = serialize_message(messages)
    print("onname에서...", serializedMsgs)

    send_client(sock, serializedMsgs)

    # targetMember member객체에 name 바뀐 것 저장
    targetMember.name = newName

    # 2. 클라이언트가 방에 있는 경우 해당 대화방의 모든 멤버들에게 시스템 메시지 send
    text = f"{oldName}의 이름이 {newName}으로 변경되었습니다."
    if targetMember.room != None:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": text,
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsgs = serialize_message(messages)
        send_clients(
            list(filter(lambda m: m is not targetMember, targetMember.room.members)),
            serializedMsgs,
        )


def on_rooms(sock, msg):
    """
    /rooms에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    """
    roomList = [
        {
            "roomId": roomId,
            "title": room.title,
            "members": [member.name for member in room.members],
        }
        for roomId, room in rooms.items()
    ]

    messages = []
    if FLAGS.format == "json":
        message = {"type": "SCRoomsResult", "rooms": roomList}
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_ROOMS_RESULT
        messages.append(message)

        roomsResult = pb.SCRoomsResult()
        for roomId, room in rooms.items():
            r = roomsResult.rooms.add()
            r.roomId = roomId
            r.title = room.title
            for member in room.members:
                r.members.append(member.name)
        messages.append(roomsResult)

    serializedMsgs = serialize_message(messages)
    print("onrooms에서...", serializedMsgs)
    send_client(sock, serializedMsgs)


def on_create_room(sock, msg):
    """
    /create에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    :param msg: 방 제목이 담긴 message
    """
    text = "대화 방에 있을 때는 방을 개설할 수 없습니다."

    # 해당하는 멤버 찾기
    targetMember = find_member(sock)
    if not targetMember:
        print("해당하는 멤버 없음")

    if targetMember.room != None:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": text,
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsgs = serialize_message(messages)
        send_client(targetMember.sock, serializedMsgs)
        return

    # 방 이름 얻기
    if FLAGS.format == "json":
        roomTitle = msg["title"]
    else:
        roomTitle = msg.title

    newRoom = room(title=roomTitle)
    roomMutex.acquire()
    rooms[newRoom.roomId] = newRoom
    roomMutex.release()
    print(f"방[{newRoom.roomId}]: 생성. 방제 {newRoom.title}")

    if FLAGS.format == "json":
        on_join_room(sock, {"roomId": newRoom.roomId})
    else:
        # protobuf
        joinRoom = pb.CSJoinRoom()
        joinRoom.roomId = newRoom.roomId
        on_join_room(sock, joinRoom)


def on_join_room(sock, msg):
    """
    /join에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    """
    text = "대화 방에 있을 때는 다른 방에 들어갈 수 없습니다."

    # 해당하는 멤버 찾기
    targetMember = find_member(sock)
    if not targetMember:
        print("해당하는 멤버 없음")

    if targetMember.room != None:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": text,
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsgs = serialize_message(messages)
        send_client(targetMember.sock, serializedMsgs)
        return

    # roomId 얻기
    if FLAGS.format == "json":
        roomId = msg["roomId"]
    else:  # protobuf
        roomId = msg.roomId

    if roomId in rooms:
        targetRoom = rooms[roomId]
        roomMutex.acquire()
        targetRoom.add_member(targetMember)
        roomMutex.release()
    else:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": "대화방이 존재하지 않습니다.",
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsgs = serialize_message(messages)
        send_client(targetMember.sock, serializedMsgs)
        return

    # member의 room 멤버변수에 참가한 방 정보 저장
    targetMember.room = targetRoom

    # 1. 참가한 클라이언트에게 시스템 메시지 send
    text = f"방제[{targetRoom.title}] 방에 입장했습니다."
    messages = []
    if FLAGS.format == "json":
        message = {
            "type": "SCSystemMessage",
            "text": text,
        }
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
        messages.append(message)

        message = pb.SCSystemMessage()
        message.text = text
        messages.append(message)

    serializedMsgs = serialize_message(messages)
    print("onjoinroom에서...", serializedMsgs)

    send_client(targetMember.sock, serializedMsgs)

    # 2. 타겟 방에 있는 모든 멤버(방금 참가한 클라이언트 제외)에게 새 멤버 참여에 대해 send 해야한다
    text = f"{targetMember.name}님이 입장했습니다."
    messages = []
    if FLAGS.format == "json":
        message = {
            "type": "SCSystemMessage",
            "text": text,
        }
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
        messages.append(message)

        message = pb.SCSystemMessage()
        message.text = text
        messages.append(message)

    serializedMsgs = serialize_message(messages)
    send_clients(
        list(filter(lambda m: m is not targetMember, targetRoom.members)),
        serializedMsgs,
    )


def on_leave_room(sock, msg):
    """
    /leave에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    :param msg: 이용x
    """
    text = "현재 대화방에 들어가 있지 않습니다."
    # 해당하는 멤버 찾기
    targetMember = find_member(sock)
    if not targetMember:
        print("해당하는 멤버 없음")

    if targetMember.room == None:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": text,
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsg = serialize_message(messages)
        send_client(targetMember.sock, serializedMsg)
        return

    # 퇴장
    targetRoom = targetMember.room
    roomMutex.acquire()
    targetRoom.remove_member(targetMember)
    roomMutex.release()
    targetMember.room = None
    text = f"방제[{targetRoom.title}] 대화 방에서 퇴장했습니다."

    messages = []
    if FLAGS.format == "json":
        message = {
            "type": "SCSystemMessage",
            "text": text,
        }
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
        messages.append(message)

        message = pb.SCSystemMessage()
        message.text = text
        messages.append(message)

    serializedMsg = serialize_message(messages)
    print("onleaveroom에서...", serializedMsg)

    # 1. 퇴장한 클라이언트에게 시스템 메시지 send
    send_client(targetMember.sock, serializedMsg)

    # 2-1. 타겟 방에 혼자만 남아있었는데 퇴장한 경우 해당 방은 폭파
    if len(targetRoom.members) == 0:
        print(f"방[{targetRoom.roomId}]: 명시적 /leave 명령으로 인한 방폭")
        roomMutex.acquire()
        del rooms[targetRoom.roomId]
        roomMutex.release()
        return

    # 2-2. 타겟 방에 있는 모든 멤버(방금 참가한 클라이언트 제외)에게 멤버 퇴장에 대해 send 해야한다
    text = f"{targetMember.name}님이 퇴장했습니다."
    messages = []
    if FLAGS.format == "json":
        message = {
            "type": "SCSystemMessage",
            "text": text,
        }
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
        messages.append(message)

        message = pb.SCSystemMessage()
        message.text = text
        messages.append(message)

    serializedMsg = serialize_message(messages)
    send_clients(
        list(filter(lambda m: m is not targetMember, targetRoom.members)),
        serializedMsg,
    )


def on_chat(sock, msg):
    """
    일반 채팅에 대한 처리 담당 메시지 핸들러

    :param sock: 메시지 발생시킨 클라이언트 소켓
    """
    text = "현재 대화방에 들어가 있지 않습니다."
    # 해당하는 멤버 찾기
    targetMember = find_member(sock)
    if not targetMember:
        print("해당하는 멤버 없음")

    if targetMember.room == None:
        messages = []
        if FLAGS.format == "json":
            message = {
                "type": "SCSystemMessage",
                "text": text,
            }
            messages.append(message)
        else:
            # protobuf
            message = pb.Type()
            message.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(message)

            message = pb.SCSystemMessage()
            message.text = text
            messages.append(message)

        serializedMsg = serialize_message(messages)
        send_client(targetMember.sock, serializedMsg)
        return
    targetRoom = targetMember.room

    # 1. 타겟 방에 있는 모든 멤버(방금 참가한 클라이언트 제외)에게 채팅 메시지를 send 해야한다

    messages = []
    if FLAGS.format == "json":
        message = {
            "type": "SCChat",
            "member": targetMember.name,
            "text": msg["text"],
        }
        messages.append(message)
    else:
        # protobuf
        message = pb.Type()
        message.type = pb.Type.MessageType.SC_CHAT
        messages.append(message)

        message = pb.SCChat()
        message.member = targetMember.name
        message.text = msg.text
        messages.append(message)

    serializedMsg = serialize_message(messages)
    send_clients(
        list(filter(lambda m: m is not targetMember, targetRoom.members)), serializedMsg
    )


def on_shutdown(sock, msg):
    """
    서버 종료를 처리를 위한 메시지 핸들러

    :param sock: 이용x
    :param msg: 이용x
    """
    print("서버 중지가 요청됨")
    # 쓰레드 종료
    print("쓰레드를 종료합니다")
    quit.set()
    os.write(wakeup_pipe[1], b"x")


jsonMsgHandler = {
    "CSName": on_name,
    "CSRooms": on_rooms,
    "CSCreateRoom": on_create_room,
    "CSJoinRoom": on_join_room,
    "CSLeaveRoom": on_leave_room,
    "CSChat": on_chat,
    "CSShutdown": on_shutdown,
}

protobufMsgHandler = {
    pb.Type.MessageType.CS_NAME: on_name,
    pb.Type.MessageType.CS_ROOMS: on_rooms,
    pb.Type.MessageType.CS_CREATE_ROOM: on_create_room,
    pb.Type.MessageType.CS_JOIN_ROOM: on_join_room,
    pb.Type.MessageType.CS_LEAVE_ROOM: on_leave_room,
    pb.Type.MessageType.CS_CHAT: on_chat,
    pb.Type.MessageType.CS_SHUTDOWN: on_shutdown,
}

protobufMsgParser = {
    pb.Type.MessageType.CS_NAME: pb.CSName.FromString,
    pb.Type.MessageType.CS_ROOMS: pb.CSRooms.FromString,
    pb.Type.MessageType.CS_CREATE_ROOM: pb.CSCreateRoom.FromString,
    pb.Type.MessageType.CS_JOIN_ROOM: pb.CSJoinRoom.FromString,
    pb.Type.MessageType.CS_LEAVE_ROOM: pb.CSLeaveRoom.FromString,
    pb.Type.MessageType.CS_CHAT: pb.CSChat.FromString,
    pb.Type.MessageType.CS_SHUTDOWN: pb.CSShutdown.FromString,
}


def handle_message(sock, data):
    """
    message를 format에 맞게 handler map을 이용하여 처리
    """
    global currentProtobufType

    print("handle")
    if FLAGS.format == "json":
        msg = json.loads(data)
        print(f"json형태:{msg}")
        msgType = msg["type"]
        if msgType not in jsonMsgHandler:
            print("해당하는 타입 없음")
        else:
            jsonMsgHandler[msgType](sock, msg)
    else:
        """protobuf 처리"""
        if currentProtobufType == None:
            # 타입 복구
            msg = pb.Type.FromString(data)
            if msg.type in protobufMsgParser and msg.type in protobufMsgHandler:
                currentProtobufType = msg.type
            else:
                print("해당하는 타입 없음")
        else:
            # 타입은 아는 상태임. parser로 복구 후 handler로 보낸다
            msg = protobufMsgParser[currentProtobufType](data)
            try:
                protobufMsgHandler[currentProtobufType](sock, msg)
            finally:
                currentProtobufType = None


def handle_client():
    """
    클라이언트 동작 처리 Thread
    """
    while not quit.is_set():
        # task를 빼낸다
        taskMutex.acquire()
        while not taskQueue:
            print("wait중...")
            taskFilled.wait()
        clientSock, task = taskQueue.pop(0)
        taskMutex.release()

        handle_message(clientSock, task)
    print("quit 불림")


def serialize_message(messages):
    """
    메시지를 format('json' 또는 'Protobuf')에 맞게 직렬화

    :param messages: 직렬화하기 위한 메시지 리스트
    """
    serializedMsgs = []
    for message in messages:
        if FLAGS.format == "json":
            serialized = bytes((json.dumps(message)), encoding="utf-8")
        else:
            # protobuf
            serialized = message.SerializeToString()
        serializedMsgs.append(serialized)
    return serializedMsgs


def send_client(clientSock, task):
    """
    taskQueue의 task를 client에게 send
    """
    for t in task:
        toSend = len(t)
        toSendBigEndian = int.to_bytes(toSend, byteorder="big", length=2)
        t = toSendBigEndian + t

        offset = 0
        while offset < len(t):
            numSent = clientSock.send(t[offset:])
            if numSent <= 0:
                print("send 실패")
            offset += numSent


def send_clients(members, task):
    """
    task를 특정 대화방의 client들에게 send
    """
    for member in members:
        send_client(member.sock, task)


def recv_client(eventSock):
    """
    클라이언트 메시지 recv 후 taskQueue에 넣는다

    :param eventSock: listen()하는 서버 측 소켓
    """
    global currentMessageLen
    global socketBuf

    recvBuf = eventSock.recv(65535)
    if not recvBuf:
        print("recv 실패")
        raise SocketClosed()

    if not socketBuf:
        socketBuf = recvBuf
    else:
        socketBuf += recvBuf

    while True:
        if currentMessageLen is None:
            if len(socketBuf) < 2:
                return

            currentMessageLen = int.from_bytes(socketBuf[0:2], byteorder="big")
            socketBuf = socketBuf[2:]

        if len(socketBuf) < currentMessageLen:
            return

        data = socketBuf[:currentMessageLen]
        socketBuf = socketBuf[currentMessageLen:]
        currentMessageLen = None

        print(f"taskQueue에 넣기 전: {data}")
        # taskQueue에 작업을 넣어준다
        taskMutex.acquire()
        taskQueue.append((eventSock, data))
        print("taskQueue에 append함")
        taskFilled.notify()
        print("notify도 함")
        taskMutex.release()


def accept_client(passiveSock):
    """
    클라이언트 연결 accept 처리

    :param passiveSock: listen()하는 서버 측 소켓
    """

    clientSock, address = passiveSock.accept()
    clientSocks.append(clientSock)
    inputs.append(clientSock)

    newMember = add_to_members(sock=clientSock, name=f"{(address[0], address[1])}")
    print(f"새로운 클라이언트 접속: {newMember.name}")


def main(argv):

    # client 요청 처리하는 Thread들 (default: 2개)
    workerThreads = [
        threading.Thread(target=handle_client) for _ in range(FLAGS.workers)
    ]
    for i in range(len(workerThreads)):
        workerThreads[i].start()
        print(f"메시지 작업 쓰레드 #{i} 생성")

    # 클라이언트 연결 요청 받는 용도의 passiveSocket
    passiveSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    inputs.append(passiveSock)

    # setsockopt: SO_REUSEADDR
    passiveSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # bind
    passiveSock.bind((FLAGS.ip, FLAGS.port))

    # listen
    passiveSock.listen(10)
    print(f"Port 번호 {FLAGS.port}에서 서버 작동 중")

    global wakeup_pipe
    wakeup_pipe = os.pipe()
    inputs.append(wakeup_pipe[0])

    # 소켓 연결 받기
    while True:
        try:
            readables, writeables, exceptions = select.select(inputs, [], [], None)

            for eventSock in readables:
                if passiveSock is eventSock:
                    accept_client(passiveSock)
                elif eventSock == wakeup_pipe[0]:
                    os.read(wakeup_pipe[0], 1)
                else:  # client로부터 message 수신
                    recv_client(eventSock)
        except SocketClosed:
            print("소켓 닫음")
            break
        except NoTypeFieldInMessage:
            break
        except UnknownTypeInMessage as err:
            print(f"핸들러에 등록되지 않은 메시지 타입 {err}")
            break
        except socket.error as err:
            if err.errno == errno.ECONNRESET:
                print("소켓 닫음")
            else:
                print(f"소켓 에러: {err}")
            break

    # thread들 join()
    print("Main thread 종료 중")
    for i in range(len(workerThreads)):
        print(f"작업 쓰레드 join() 시작")
        workerThreads[i].join()
        print(f"작업 쓰레드 join() 완료")

    # socket close
    print("소켓 close()")
    for sock in inputs:
        if isinstance(sock, socket.socket):
            sock.close()
        else:
            os.close(sock)


if __name__ == "__main__":
    app.run(main)
