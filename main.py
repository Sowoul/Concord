import eventlet
eventlet.monkey_patch(
    os=True,
    select=True,
    socket=True,
    thread=True,
    time=True
)

from flask import Flask, render_template, redirect, url_for, request, session, jsonify
from flask_socketio import SocketIO, join_room, emit
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from string import ascii_uppercase
from random import choice


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.sqlite3"
app.config["SECRET_KEY"] = "OJASWA"
socket = SocketIO(app, async_mode='eventlet')
db = SQLAlchemy(app)


class Room(db.Model):
    id = db.Column("id", db.String(8), primary_key=True)
    users = db.relationship("User", backref="room", lazy=True)
    folders = db.relationship("Folder", backref="room", lazy=True)

    def __init__(self, id):
        self.id = id


class User(db.Model):
    name = db.Column("name", db.String(16), primary_key=True)
    paswd = db.Column("passwd", db.String(200))
    room_id = db.Column(db.String(8), db.ForeignKey("room.id"), nullable=True)

    def __init__(self, name, paswd, room_id=None):
        self.name = name
        self.paswd = paswd
        self.room_id = room_id


class Folder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    room_id = db.Column(db.String(8), db.ForeignKey("room.id"), nullable=False)
    parent_folder_id = db.Column(db.Integer, db.ForeignKey("folder.id"), nullable=True)
    subfolders = db.relationship(
        "Folder", backref=db.backref("parent_folder", remote_side=[id]), lazy=True
    )
    files = db.relationship("File", backref="folder", lazy=True)

    def __init__(self, name, room_id, parent_folder_id=None):
        self.name = name
        self.room_id = room_id
        self.parent_folder_id = parent_folder_id


class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id"), nullable=False)

    def __init__(self, name, content, folder_id):
        self.name = name
        self.content = content
        self.folder_id = folder_id


def genroom(ln):
    while True:
        temp = "".join(choice(ascii_uppercase) for _ in range(ln))
        if not Room.query.get(temp):
            return temp


@app.route("/")
def red():
    name = session.get("username", "")
    if name == "":
        return redirect(url_for("login"))
    return redirect(url_for("code"))


@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    if request.method == "POST":
        username = request.form.get("username", "").lower()
        password = request.form.get("password", "")
        room = request.form.get("room", "")
        dude = db.session.query(User).filter_by(name=username).first()
        if dude is None:
            return render_template(
                "login.html", error="The entered user does not exist"
            )
        if not check_password_hash(dude.paswd, password):
            return render_template(
                "login.html", error="The entered password is incorrect"
            )
        if room == "":
            room = genroom(8)
            new_room = Room(id=room)
            db.session.add(new_room)
        else:
            existing_room = Room.query.get(room)
            if existing_room is None:
                return render_template(
                    "login.html", error="The entered room does not exist"
                )

        dude.room_id = room
        db.session.commit()

        session["username"] = username
        session["room"] = room
        return redirect(url_for("code"))
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    session.clear()
    if request.method == "POST":
        username = request.form.get("username", "").lower()
        password = request.form.get("password", "")
        dude = db.session.query(User).filter_by(name=username).first()
        if dude is not None:
            return render_template("signup.html", error="The username is taken!")
        if password == "":
            return render_template("signup.html", error="The password can not be empty")
        newuser = User(username, generate_password_hash(password))
        db.session.add(newuser)
        db.session.commit()
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/getinfo", methods=["GET"])
def getinfo():
    username = session.get("username", "")
    room = session.get("room", "")
    return {"username": username, "room": room}


@app.route("/code", methods=["GET", "POST"])
def code():
    username = session.get("username", "")
    room = session.get("room", "")
    if username == "" or room == "":
        return redirect(url_for("login"))
    if db.session.query(User.name).filter_by(name=username).first() is None:
        return redirect(url_for('login'))
    return render_template("index.html")


@app.route("/get_file_tree", methods=["GET"])
def get_file_tree():
    room_id = request.args.get("room")
    folders = Folder.query.filter_by(room_id=room_id, parent_folder_id=None).all()

    def build_tree(folder):
        return {
            "name": folder.name,
            "subfolders": [build_tree(subfolder) for subfolder in folder.subfolders],
            "files": [{"name": file.name} for file in folder.files],
        }

    tree_data = [build_tree(folder) for folder in folders]
    return jsonify(tree_data)


@socket.on("connect")
def connected():
    room = session.get("room", "")
    username = session.get("username", "")
    join_room(room)
    lst = db.session.query(User).filter_by(room_id=room).all()
    socket.emit("newjoin", [user.name for user in lst], to=room)


@socket.on("file_update")
def handle_file_update(data):
    room_id = session.get("room", "")
    folder_name = data.get("folder_name", "")
    file_name = data.get("file_name", "")
    new_content = data.get("content", "")

    folder = Folder.query.filter_by(room_id=room_id, name=folder_name).first()
    if not folder:
        folder = Folder(name=folder_name, room_id=room_id)
        db.session.add(folder)
        db.session.commit()

    file = File.query.filter_by(folder_id=folder.id, name=file_name).first()
    if not file:
        file = File(name=file_name, content=new_content, folder_id=folder.id)
        db.session.add(file)
    else:
        file.content = new_content

    db.session.commit()
    emit("file_update", data, to=room_id)


@socket.on("load_file")
def load_file(data):
    room_id = session.get("room", "")
    folder_name = data.get("folder_name", "")
    subfolder_name = data.get("subfolder_name", "")
    file_name = data.get("file_name", "")

    folder = Folder.query.filter_by(room_id=room_id, name=folder_name).first()
    if subfolder_name:
        folder = Folder.query.filter_by(
            room_id=room_id, name=subfolder_name, parent_folder_id=folder.id
        ).first()
    file = File.query.filter_by(folder_id=folder.id, name=file_name).first()

    if file:
        emit("file_loaded", {"content": file.content})
    else:
        emit("file_loaded", {"content": ""})


@socket.on("create_folder")
def create_folder(data):
    room_id = session.get("room", "")
    folder_name = data.get("folder_name", "")
    parent_folder_id = data.get("parent_folder_id", "")
    if parent_folder_id == "":
        existing_folder = Folder.query.filter_by(room_id=room_id, name=folder_name).first()
        if not existing_folder:
            new_folder = Folder(name=folder_name, room_id=room_id)
            db.session.add(new_folder)
            db.session.commit()

    else:
        existing_folder = Folder.query.filter_by(room_id=room_id, name=folder_name, parent_folder_id=parent_folder_id).first()
        if not existing_folder:
            new_folder = Folder(name=folder_name, room_id=room_id, parent_folder_id=parent_folder_id)
            db.session.add(new_folder)
            db.session.commit()

    emit("folder_created", {"folder_name": folder_name, "parent_folder_id": parent_folder_id}, to=room_id)


@socket.on("create_file")
def create_file(data):
    room_id = session.get("room", "")
    folder_name = data.get("folder_name", "")
    file_name = data.get("file_name", "")
    parent_folder_id = data.get("parent_folder_id", None)

    folder = Folder.query.filter_by(
        room_id=room_id, name=folder_name, parent_folder_id=parent_folder_id
    ).first()
    if not folder:
        folder = Folder(name=folder_name, room_id=room_id, parent_folder_id=parent_folder_id)
        db.session.add(folder)
        db.session.commit()

    existing_file = File.query.filter_by(folder_id=folder.id, name=file_name).first()
    if not existing_file:
        new_file = File(name=file_name, content="", folder_id=folder.id)
        db.session.add(new_file)
        db.session.commit()

    emit("file_created", {"file_name": file_name, "parent_folder_id": parent_folder_id}, to=room_id)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socket.run(app=app, host='0.0.0.0', port=8080, debug=True)
