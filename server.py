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
from werkzeug.utils import secure_filename

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


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """ Enforces sqlite foreign key constrains """
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def login_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
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


def get_team(comp_id, user_id):
    user = get_user()
    if not user:
        return None

    team = db.query('SELECT * FROM teams t JOIN team_player tp ON t.id = tp.id_team AND tp.id_user = :user_id AND t.comp_id = :comp_id LIMIT 1',
                    user_id=user['id'], comp_id=comp_id)
    team = list(team)

    if len(team) == 0:
        return None
    return team[0]


def get_flags():
    """Returns the flags of the current user"""

    flags = db.query('select f.task_id from flags f where f.user_id = :user_id',
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


@app.route('/')
def index():
    """Displays the main page"""

    user = get_user()

    # Render template
    render = render_template('main.html', lang=lang, user=user)
    return make_response(render)


@app.route('/error/<msg>')
def error(msg):
    """Displays an error message"""

    if msg in lang['error']:
        message = lang['error'][msg]
    else:
        message = lang['error']['unknown']

    user = get_user()

    render = render_template('error.html', lang=lang, message=message, user=user)
    return make_response(render)


def session_login(username):
    """Initializes the session with the current user's id"""
    user = db['users'].find_one(username=username)
    session['user_id'] = user['id']


@app.route('/login', methods = ['GET'])
def login_page():
    user = get_user()
    if user:
        return redirect('/competition/1')

    render = render_template('login.html', lang=lang)
    return make_response(render)


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
            #return redirect('/competitions')
            return redirect('/competition/1')

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

        #return redirect('/competitions')
        return redirect('/competition/1')

    return redirect('/error/invalid_credentials')


@app.route('/logout')
@login_required
def logout():
    """Logs the current user out"""

    del session['user_id']
    return redirect('/')


@app.route('/competitions')
@login_required
def competitions():
    """Displays past competitions"""

    user = get_user()
    competitions = db.query('''select * from competitions''')

    competitions = list(competitions)

    # Render template
    render = render_template('competitions.html', lang=lang,
        user=user, competitions=competitions)
    return make_response(render)


def competition_page(comp_id, page, **kwargs):
    user = get_user()
    team = get_team(comp_id, user['id'])

    if not team:
        return redirect('/competition/'+comp_id+'/team-register')

    competition = db['competitions'].find_one(id=comp_id)
    if not competition:
        return redirect('/error/competition_not_found')

    categories = list(db['categories'].all())

    tasks = db.query("SELECT * FROM tasks t, task_competition tc WHERE t.id = tc.task_id AND tc.comp_id = :comp_id", comp_id=comp_id)
    tasks = sorted(list(tasks), key=lambda x: x['score'])

    render = render_template('competition.html', lang=lang,
                             user=user, competition=competition, categories=categories,
                             tasks=tasks, page=page, team=team, **kwargs)
    return make_response(render)


@app.route('/competition/<comp_id>/')
@login_required
def competition(comp_id):
    return competition_page(comp_id, None)


@app.route('/competition/<comp_id>/edit', methods=['GET'])
@admin_required
def competition_edit(comp_id):
    competition = db['competitions'].find_one(id=comp_id)
    categories = list(db['categories'].all())

    tasks_comp = db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND tc.comp_id = :comp_id", comp_id=comp_id)
    tasks_comp = list(tasks_comp)

    tasks = db.query("SELECT * FROM tasks WHERE id NOT IN (SELECT id FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND tc.comp_id = :comp_id)", comp_id=comp_id)
    tasks = list(tasks)

    render = render_template('competition-edit.html', lang=lang,
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
        return jsonify({'message': 'Internal error!'}), 400
    else:
        if not db['tasks'].find_one(id=task_id) or not db['competitions'].find_one(id=comp_id):
            return jsonify({'message': 'Invalid task or competition!'}), 400

        task_competition = db['task_competition']
        entry = dict(task_id=task_id, comp_id=comp_id, score=score)

        task_competition.insert(entry)

        task = list(db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = :task_id AND tc.task_id = :task_id AND tc.comp_id = :comp_id LIMIT 1",
                        task_id = task_id, comp_id = comp_id))
        return jsonify(task[0]), 200


@app.route('/competition/<comp_id>/edittask', methods=['POST'])
@admin_required
def competition_edit_task(comp_id):
    try:
        comp_id = int(comp_id)
        task_id = int(request.form['task-id']);
        score = int(request.form['task-score']);
    except KeyError:
        return jsonify({'message': 'Internal error!'}), 400
    else:
        task_competition = db['task_competition']
        entry = task_competition.find_one(task_id = task_id, comp_id = comp_id)
        if not entry:
            return jsonify({'message': 'Not found'}), 400

        entry['score'] = score
        task_competition.update(entry, ['task_id', 'comp_id'])

        task = list(db.query("SELECT * FROM tasks t JOIN task_competition tc ON t.id = :task_id AND tc.task_id = :task_id AND tc.comp_id = :comp_id LIMIT 1",
                        task_id = task_id, comp_id = comp_id))
        return jsonify(task[0]), 200


@app.route('/competition/<comp_id>/removetask', methods=['POST'])
@admin_required
def competition_remove_task(comp_id):
    try:
        comp_id = int(comp_id)
        task_id = int(request.form['task-id']);
    except KeyError:
        return jsonify({'message': "Internal error!"}), 400
    else:
        db['task_competition'].delete(task_id = task_id, comp_id = comp_id)
        task = db['tasks'].find_one(id = task_id)
        return jsonify(task), 200


@app.route('/competition/<comp_id>/task/<task_id>', methods=['GET'])
@login_required
def competition_task(comp_id, task_id):
    task = db['tasks'].find_one(id=task_id)
    return competition_page(comp_id, 'competition-task.html', task=task)


@app.route('/competition/<comp_id>/task/<task_id>', methods=['POST'])
@login_required
def competition_task_post(comp_id, task_id):
    user = get_user()
    if get_team(comp_id, user['id']) is None:
        return jsonify({}), 400

    task = db['tasks'].find_one(id=task_id)
    render = render_template('competition-task.html', lang=lang, task=task)
    return render, 200


@app.route('/competition/<comp_id>/team', methods=['GET'])
@login_required
def competition_team(comp_id):
    return competition_page(comp_id, 'competition-team.html')


@app.route('/competition/<comp_id>/team', methods=['POST'])
@login_required
def competition_team_post(comp_id):
    user = get_user()
    team = get_team(comp_id, user['id'])
    if not team:
        return jsonify({}), 400

    render = render_template('competition-team.html', lang=lang, team=team)
    return make_response(render), 200


@app.route('/competition/<comp_id>/team-register', methods=['GET'])
@login_required
def competition_team_register(comp_id):
    user = get_user()
    team = get_team(comp_id, user['id'])
    if team:
        return redirect('/competition/'+comp_id+'/team')

    render = render_template('team-register.html', lang=lang, user=user)
    return make_response(render), 200

@app.route('/competition/<comp_id>/team-register', methods=['POST'])
@login_required
def competition_team_register_post(comp_id):
    secret = request.form['secret']
    # TODO

    if 'register-button' in request.form:
        try:
            name = bleach.clean(request.form['team-name'], tags=[])
        except KeyError:
            return redirect('/error/form')
        else:
            if len(name) == 0:
                return redirect('/error/form')

            teams = db['teams']
            team_secret = hashlib.md5(str(datetime.datetime.utcnow())).hexdigest()
            team = dict(
                name=name,
                hash=team_secret,
                comp_id=comp_id
            )
            teams.insert(team)

            id_team = teams.find_one(hash=team_secret)['id']
            team_player = db['team_player']
            team_player.insert(dict(id_team=id_team, id_user=session['user_id']))

            #return redirect('/competitions')
            return redirect('/competition/1')

    if 'join-button' in request.form:
        try:
            team_secret = bleach.clean(request.form['team-secret'], tags=[])
        except KeyError:
            return redirect('/error/form')
        else:
            team = db.query("SELECT * FROM teams WHERE hash = :team_secret AND comp_id = :comp_id", team_secret=team_secret, comp_id=comp_id)
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

            #return redirect('/competitions')
            return redirect('/competition/1')



@app.route('/teamsign/<comp_id>')
def teamsign(comp_id):
    user = get_user()

    render = render_template('teamsign.html', lang=lang,
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







def store_filename(file):
    filename, ext = os.path.splitext(file.filename)
    #hash current time for file name
    filename = secure_filename(filename) + '_' + hashlib.md5(str(datetime.datetime.utcnow())).hexdigest()
    #if upload has extension, append to filename
    if ext:
        filename = filename + ext

    file.save(os.path.join("static/files/", filename))
    return filename



@app.route('/tasks/', methods=['GET'])
@admin_required
def tasks():
    categories = list(db['categories'].all())

    tasks = db.query('SELECT * FROM tasks')
    tasks = list(tasks)

    user = get_user()
    render = render_template('tasks.html', lang=lang, user=user,
            categories=categories, tasks=tasks)
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
            return jsonify({'message': 'No flag set'}), 400
    except KeyError:
        return jsonify({'message': 'Form incorrect filled'}), 400
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
            task["file"] = store_filename(file)

        tasks.insert(task)

        task = tasks.find_one(name = task["name"], flag = task["flag"])
        return jsonify(task), 200


@app.route('/task/edit', methods=['POST'])
@admin_required
def task_edit():
    try:
        tid = request.form['task-id']
        name = bleach.clean(request.form['task-name'], tags=[])
        desc = bleach.clean(request.form['task-desc'], tags=descAllowedTags)
        category = int(request.form['task-category'])
        hint = request.form['task-hint']
        flag = request.form['task-flag']
        if not flag:
            return jsonify({ 'message': 'No flag set' }), 400
    except KeyError:
        return jsonify({ 'message': 'Form incorrect filled' }), 400
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
            filename = store_filename(file)

            #remove old file
            if task['file']:
                os.remove(os.path.join("static/files/", task['file']))

            task["file"] = filename

        tasks.update(task, ['id'])
        task = tasks.find_one(name = task["name"], flag = task["flag"])
        return jsonify(task)


@app.route('/task/delete', methods=['POST'])
@admin_required
def task_delete():
    task_id = int(request.form['task-id'])

    task = db['tasks'].find_one(id=task_id)

    if task is None:
        return jsonify({ 'message': 'Task not found!' }), 400

    if task['file']:
        os.remove(os.path.join("static/files/", task['file']))
    db['tasks'].delete(id=task_id)
    return jsonify({}), 200


@app.route('/task/<task_id>', methods=['GET', 'POST'])
@admin_required
def task_get(task_id):
    task = db["tasks"].find_one(id=task_id)
    if not task:
        return jsonify({ 'message': 'Not found' }), 400
    return jsonify(task)


@app.route('/task_competition/<cid>-<tid>', methods=['GET', 'POST'])
@admin_required
def task_competition_get(cid, tid):
    task = list(db.query('SELECT * FROM tasks t JOIN task_competition tc ON t.id = tc.task_id AND t.id = :task_id AND tc.comp_id = :comp_id LIMIT 1',
                         task_id = tid, comp_id = cid));
    if len(task) == 0:
        return jsonify({ 'message': 'Not found' }), 400
    return jsonify(task[0]), 200









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
        debug=config['debug'], threaded=True, extra_files=['config.json', config['language_file']])
