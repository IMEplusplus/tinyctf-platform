#!/usr/bin/env python

"""server.py -- the main flask server module"""

import dataset
import json
import random
import time
import hashlib
import datetime
import os
import dateparser
import bleach

from base64 import b64decode
from functools import wraps

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlite3 import Connection as SQLite3Connection
from werkzeug.contrib.fixers import ProxyFix

from flask import Flask
from flask import jsonify
from flask import make_response
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from flask import Response

app = Flask(__name__, static_folder='static', static_url_path='')

db = None
lang = None
config = None

descAllowedTags = bleach.ALLOWED_TAGS + ['br', 'pre']

def login_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('error', msg='login_required'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('error', msg='login_required'))
        user = get_user()
        if user["isAdmin"] == False:
            return redirect(url_for('error', msg='admin_required'))
        return f(*args, **kwargs)
    return decorated_function

def get_user():
    """Looks up the current user in the database"""

    login = 'user_id' in session
    if login:
        return db['users'].find_one(id=session['user_id'])

    return None

def get_task(comp_id, tid):
    """Finds a task with a given category and score"""

    task = db.query("SELECT t.*, c.name cat_name FROM tasks t JOIN categories c on c.id = t.category JOIN competitions comp ON comp.id=t.competition WHERE t.id = :tid AND t.competition = :comp_id",
            tid=tid, comp_id=comp_id)
    return list(task)[0]

def get_flags():
    """Returns the flags of the current user"""

    flags = db.query('''select f.task_id from flags f
        where f.user_id = :user_id''',
        user_id=session['user_id'])
    return [f['task_id'] for f in list(flags)]

def get_dates(comp_id):
    """Returns the end and start dates of current competition"""

    dates = db['competitions'].find_one(id=comp_id)
    return dates

def check_running(comp_id):
    """   """
    dates = get_dates(comp_id)

    startDate = datetime.datetime.strptime(dates['date_start'], "%m-%d-%y %H:%M%p").date()
    endDate = datetime.datetime.strptime(dates['date_end'], "%m-%d-%y %H:%M%p").date()

    if datetime.datetime.today().date() > startDate:	
        if datetime.datetime.today().date() < endDate:
            db.query('UPDATE competitions SET running=1 WHERE id=:comp_id', comp_id=comp_id)

@app.route('/error/<msg>')
def error(msg):
    """Displays an error message"""

    if msg in lang['error']:
        message = lang['error'][msg]
    else:
        message = lang['error']['unknown']

    user = get_user()

    render = render_template('frame.html', lang=lang, page='error.html',
        message=message, user=user)
    return make_response(render)

def session_login(username):
    """Initializes the session with the current user's id"""
    user = db['users'].find_one(username=username)
    session['user_id'] = user['id']

@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """ Enforces sqlite foreign key constrains """
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

@app.route('/login', methods = ['POST'])
def login():
    from werkzeug.security import check_password_hash

    username = request.form['username']
    password = request.form['password']

    if 'login-button' in request.form:
        """Attempts to log the user in"""

        user = db['users'].find_one(username=username)
        if user is None:
            return redirect('/error/invalid_credentials')

        if check_password_hash(user['password'], password):
            session_login(username)
            return redirect('/competitions')

    if 'register-button' in request.form:
        """Attempts to register a new user"""

        from werkzeug.security import generate_password_hash

        username = request.form['username']
        password = request.form['password']

        if not username:
            return redirect('/error/empty_user')

        user_found = db['users'].find_one(username=username)
        if user_found:
            return redirect('/error/already_registered')

        isAdmin = False
        isHidden = False
        userCount = db['users'].count()

        #if no users, make first user admin
        if userCount == 0:
            isAdmin = True
            isHidden = True

        new_user = dict(username=username,
            password=generate_password_hash(password), isAdmin=isAdmin,
            isHidden=isHidden)
        db['users'].insert(new_user)

        # Set up the user id for this session
        session_login(username)

        return redirect('/competitions')

    return redirect('/error/invalid_credentials')

@app.route('/competition/<comp_id>/')
@login_required
def competition(comp_id):
    user = get_user()
    name_team = ""
    if not user['isAdmin']:
        player_team = db.query("SELECT * FROM teams t, team_player tp WHERE tp.id_team = t.id AND t.comp_id = :comp_id AND tp.id_user = :user_id", comp_id=comp_id, user_id=session['user_id'])
        player_team = list(player_team)
        # If the normal player doesn't have a team to that competition
        if len(player_team) == 0:
            return redirect(url_for('teamsign', comp_id=comp_id))
        name_team = player_team[0]['name']

    tasks = db.query("SELECT * FROM tasks t, task_competition tc WHERE t.id = tc.task_id AND tc.comp_id = :comp_id", comp_id=comp_id)
    tasks = list(tasks)
    print tasks

    """
    tasks = []
    for t in tasks_db:
        tasks[t.category]
    """

    # Render template
    render = render_template('frame.html', lang=lang, page='competition.html',
        user=user, comp_id=comp_id, tasks=tasks, name_team=name_team)
    return make_response(render)

@app.route('/competition/<comp_id>/edit', methods=['GET'])
@admin_required
def competition_edit(comp_id):
    competition = db['competitions'].find_one(id=comp_id)
    categories = list(db['categories'].all())

    tasks_comp = db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND tc.comp_id = :comp_id", comp_id=comp_id)
    tasks_comp = list(tasks_comp)

    tasks = db.query("SELECT * FROM tasks WHERE id NOT IN (SELECT id FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND tc.comp_id = :comp_id)", comp_id=comp_id)
    tasks = list(tasks)

    render = render_template('frame.html', lang=lang, page='competition-edit.html',
                             user=get_user(), tasks_comp=tasks_comp, tasks=tasks, competition=competition, categories=categories)
    return make_response(render)

@app.route('/competition/<comp_id>/addtask', methods=['POST'])
@admin_required
def competition_add_task(comp_id):
    try:
        comp_id = int(comp_id)
        task_id = int(request.form['task-id']);
        score = int(request.form['task-score']);
    except KeyError:
        return jsonify({"status": "ERROR", "message": "Internal error!"});
    else:
        if not db['tasks'].find_one(id=task_id) or not db['competitions'].find_one(id=comp_id):
            return jsonify({"status": "ERROR", "message": "Invalid task or competition!"});

        task_competition = db['task_competition']
        entry = dict(task_id=task_id, comp_id=comp_id, score=score)

        task_competition.insert(entry)

        task = list(db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = :task_id AND tc.task_id = :task_id AND tc.comp_id = :comp_id LIMIT 1",
                        task_id = task_id, comp_id = comp_id))
        return jsonify({"status": "OK", "task" : task[0]})


@app.route('/competition/<comp_id>/edittask', methods=['POST'])
@admin_required
def competition_edit_task(comp_id):
    try:
        comp_id = int(comp_id)
        task_id = int(request.form['task-id']);
        score = int(request.form['task-score']);
    except KeyError:
        return jsonify({"status": "ERROR", "message": "Internal error!"});
    else:
        task_competition = db['task_competition']
        entry = task_competition.find_one(task_id = task_id, comp_id = comp_id)
        if not entry:
            return jsonify({"status": "ERROR", "message": "Not found"});

        entry['score'] = score
        task_competition.update(entry, ['task_id', 'comp_id'])

        task = list(db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = :task_id AND tc.task_id = :task_id AND tc.comp_id = :comp_id LIMIT 1",
                        task_id = task_id, comp_id = comp_id))
        return jsonify({"status": "OK", "task" : task[0]})


@app.route('/competition/<comp_id>/removetask', methods=['POST'])
@admin_required
def competition_remove_task(comp_id):
    try:
        comp_id = int(comp_id)
        task_id = int(request.form['task-id']);
    except KeyError:
        return jsonify({"status": "ERROR", "message": "Internal error!"});
    else:
        db['task_competition'].delete(task_id = task_id, comp_id = comp_id)
        task = db['tasks'].find_one(id = task_id)
        return jsonify({"status": "OK", "task" : task})












@app.route('/teamsign/<comp_id>')
def teamsign(comp_id):
    user = get_user()

    render = render_template('frame.html', lang=lang, page='teamsign.html',
        user=user, comp_id=comp_id)
    return make_response(render)

@app.route('/teamsign/<comp_id>', methods=['POST'])
def teamsignsubmit(comp_id):
    if bleach.clean(request.form['check'], tags=[]) == 'newTeam':
        try:
            name = bleach.clean(request.form['name'], tags=[])
        except KeyError:
            return redirect('/error/form')
        else:
            teams = db['teams']
            hash_team = hashlib.md5(name + "competicao" + str(comp_id)).hexdigest()
            team = dict(
                name=name,
                hash=hash_team,
                comp_id=comp_id
            )
            teams.insert(team)

            id_team = teams.find_one(hash=hash_team)['id']
            team_player = db['team_player']
            team_player.insert(dict(id_team=id_team, id_user=session['user_id']))

    elif bleach.clean(request.form['check'], tags=[]) == 'enterTeam':
        try:
            hash_team = bleach.clean(request.form['hash'], tags=[])
        except KeyError:
            return redirect('/error/form')
        else:
            team = db.query("SELECT * FROM teams WHERE hash = :hash_team AND comp_id = :comp_id", hash_team=hash_team, comp_id=comp_id)
            team = list(team)

            if len(team) == 0:
                return redirect('/error/wrong_hash')
            else:
                team=team[0]
                team_players = db.query("SELECT * FROM team_player WHERE id_team = :id_team", id_team=team['id'])
                team_players = list(team_players)
                if len(team_players) == 3:
                    return redirect('/error/too_many_members')
                else:
                    team_playersDB = db['team_player']
                    team_playersDB.insert(dict(id_team=team['id'], id_user= session['user_id']))

    return redirect(url_for('competition', comp_id=comp_id))

@app.route('/addcat/', methods=['GET'])
@admin_required
def addcat():
    user = get_user()
    render = render_template('frame.html', lang=lang, user=user, page='addcat.html')
    return make_response(render)

@app.route('/addcat/', methods=['POST'])
@admin_required
def addcatsubmit():
    try:
        name = bleach.clean(request.form['name'], tags=[])
    except KeyError:
        return redirect('/error/form')
    else:
        categories = db['categories']
        categories.insert(dict(name=name))

        return redirect('/competitions')



@app.route('/addcompetition/', methods=['GET'])
@admin_required
def addcompetition():
    user = get_user()

    render = render_template('frame.html', lang=lang, user=user, page='addcompetition.html')
    return make_response(render)

@app.route('/addcompetition/', methods=['POST'])
@admin_required
def addcompetitionsubmit():
    try:
        name = bleach.clean(request.form['name'], tags=descAllowedTags)
        desc = bleach.clean(request.form['desc'], tags=descAllowedTags)
        #date_start = bleach.clean(request.form['date_start'])
    except KeyError:
        return redirect('/error/form')

    else:

        competitions = db['competitions']
        competition = dict(
            name=name,
            desc=desc
            #date_start=date_start
            )

        competitions.insert(competition)
        return redirect('/competitions')








@app.route('/tasks/', methods=['GET'])
@admin_required
def tasks():
    categories = list(db['categories'].all())

    tasks = db.query('SELECT * FROM tasks')
    tasks = list(tasks)

    user = get_user()
    render = render_template('frame.html', lang=lang, user=user,
            categories=categories, tasks=tasks, page='tasks.html')
    return make_response(render)


@app.route('/task/add', methods=['POST'])
@admin_required
def task_add():
    try:
        name = bleach.clean(request.form['task-name'], tags=[])
        desc = bleach.clean(request.form['task-desc'], tags=descAllowedTags)
        category = int(request.form['task-category'])
        hint = request.form['task-hint']
        flag = request.form['task-flag']
        if not flag:
            return jsonify({'status': 'ERROR', 'message': 'No flag set'})
    except KeyError:
        return jsonify({'status': 'ERROR', 'message': 'Form incorrect filled'})
    else:
        tasks = db['tasks']
        task = dict(
                name=name,
                desc=desc,
                category=category,
                hint=hint,
                flag=flag)
        file = request.files['task-file']

        if file:
            filename, ext = os.path.splitext(file.filename)
            #hash current time for file name
            filename = hashlib.md5(str(datetime.datetime.utcnow())).hexdigest()
            #if upload has extension, append to filename
            if ext:
                filename = filename + ext
            file.save(os.path.join("static/files/", filename))
            task["file"] = filename

        tasks.insert(task)

        task = tasks.find_one(name = task["name"], flag = task["flag"])
        return jsonify({"status": "OK", "task" : task})


@app.route('/task/edit', methods=['POST'])
@admin_required
def task_edit():
    print request.form
    try:
        tid = request.form['task-id']
        name = bleach.clean(request.form['task-name'], tags=[])
        desc = bleach.clean(request.form['task-desc'], tags=descAllowedTags)
        category = int(request.form['task-category'])
        hint = request.form['task-hint']
        flag = request.form['task-flag']
        if not flag:
            return jsonify({'status': 'ERROR', 'message': 'No flag set'})
    except KeyError:
        return jsonify({'status': 'ERROR', 'message': 'Form incorrect filled'})
    else:
        tasks = db['tasks']
        task = tasks.find_one(id=tid)
        task['name'] = name
        task['desc'] = desc
        task['category'] = category
        task['hint'] = hint
        task['flag'] = flag

        file = request.files['task-file']

        if file:
            filename, ext = os.path.splitext(file.filename)
            #hash current time for file name
            filename = hashlib.md5(str(datetime.datetime.utcnow())).hexdigest()
            #if upload has extension, append to filename
            if ext:
                filename = filename + ext
            file.save(os.path.join("static/files/", filename))

            #remove old file
            if task['file']:
                os.remove(os.path.join("static/files/", task['file']))

            task["file"] = filename

        tasks.update(task, ['id'])
        task = tasks.find_one(name = task["name"], flag = task["flag"])
        return jsonify({"status": "OK", "task" : task})


@app.route('/task/delete', methods=['POST'])
@admin_required
def task_delete():
    # TODO: Delete file if any
    db['tasks'].delete(id=request.form['task-id'])
    return jsonify({"status": "OK"})


@app.route('/task/<tid>', methods=['GET', 'POST'])
@admin_required
def task_get(tid):
    task = db["tasks"].find_one(id=tid)
    if not task:
        return jsonify({ 'status': 'ERROR', 'message': 'Not found' })
    return jsonify(task)

@app.route('/task_competition/<cid>-<tid>', methods=['GET', 'POST'])
@admin_required
def task_competition_get(cid, tid):
    task = list(db.query('SELECT * FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND t.id = :task_id AND tc.comp_id = :comp_id LIMIT 1',
                         task_id = tid, comp_id = cid));
    if len(task) == 0:
        return jsonify({ 'status': 'ERROR', 'message': 'Not found' })
    return jsonify(task[0])









@app.route('/submit/<comp_id>/<tid>/<flag>')
@login_required
def submit(comp_id, tid, flag):
    """Handles the submission of flags"""

    user = get_user()

    task = get_task(comp_id, tid)
    flags = get_flags()
    task_done = task['id'] in flags

    result = {'success': False}
    if not task_done and task['flag'] == b64decode(flag):

        timestamp = int(time.time() * 1000)
        ip = request.remote_addr
        print "flag submitter ip: {}".format(ip)

        # Insert flag
        new_flag = dict(task_id=task['id'], user_id=session['user_id'],
            score=task["score"], timestamp=timestamp, ip=ip)
        db['flags'].insert(new_flag)

        result['success'] = True

    return jsonify(result)

@app.route('/scoreboard/<comp_id>/')
@login_required
def scoreboard(comp_id):
    """Displays the scoreboard"""

    user = get_user()
    scores = db.query("select u.username, ifnull(sum(f.score), 0) as score, max(timestamp) as last_submit, t.competition FROM users u left join flags f ON u.id = f.user_id LEFT JOIN tasks t ON f.task_id = t.id where u.isHidden = 0 AND t.competition = :comp_id group by u.username order by score desc, last_submit asc", comp_id=comp_id)

    scores = list(scores)

    # Render template
    render = render_template('frame.html', lang=lang, page='scoreboard.html',
        user=user, scores=scores)
    return make_response(render)


@app.route('/scoreboard.json')
def scoreboard_json():
    scores = db.query('''select u.username, ifnull(sum(f.score), 0) as score,
        max(timestamp) as last_submit from users u left join flags f
        on u.id = f.user_id where u.isHidden = 0 group by u.username
        order by score desc, last_submit asc''')

    scores = list(scores)

    return Response(json.dumps(scores), mimetype='application/json')



@app.route('/competitions')
@login_required
def competitions():
    """Displays past competitions"""

    user = get_user()
    competitions = db.query('''select * from competitions''')

    competitions = list(competitions)
    
    # Render template
    render = render_template('frame.html', lang=lang, page='competitions.html',
        user=user, competitions=competitions)
    return make_response(render)

@app.route('/delete/<postID>', methods=['POST'])
@login_required
def deleteCompetitions(postID):

    user = get_user()
    if user["isAdmin"]:
        competitions = db.query('''delete from competitions where id = ''' + postID)
        #flash('Lista deletada com sucesso')
    
    return redirect('/competitions')

@app.route('/competitions.json')
def competitions_json():
    competitions = db.query('''select * from competitions''')

    competitions = list(competitions)

    return Response(json.dumps(competitions), competitions=competitions, mimetype='application/json')

@app.route('/about')
@login_required
def about():
    """Displays the about menu"""

    user = get_user()

    # Render template
    render = render_template('frame.html', lang=lang, page='about.html',
        user=user)
    return make_response(render)


@app.route('/chat')
@login_required
def chat():
    """Displays the IRC chat"""

    user = get_user()

    # Render template
    render = render_template('frame.html', lang=lang, page='chat.html', user=user)
    return make_response(render)


@app.route('/logout')
@login_required
def logout():
    """Logs the current user out"""

    del session['user_id']
    return redirect('/')

@app.route('/')
def index():
    """Displays the main page"""

    user = get_user()

    # Render template
    render = render_template('frame.html', lang=lang, page='main.html', user=user)
    return make_response(render)

"""Initializes the database and sets up the language"""

# Load config
config_str = open('config.json', 'rb').read()
config = json.loads(config_str)

app.secret_key = config['secret_key']

# Load language
lang_str = open(config['language_file'], 'rb').read()
lang = json.loads(lang_str)

# Only a single language is supported for now
lang = lang[config['language']]

# Connect to database
db = dataset.connect(config['db'])

if config['isProxied']:
    app.wsgi_app = ProxyFix(app.wsgi_app)

if __name__ == '__main__':
    # Start web server
    app.run(host=config['host'], port=config['port'],
        debug=config['debug'], threaded=True)
