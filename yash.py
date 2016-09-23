#!/usr/bin/env python
#-*-encoding: utf-8 -*-

import os, sys, codecs, re
import markdown2 as markdown
import bottle
from bottle import route, run, template, static_file, get, post, view, request, response, TEMPLATE_PATH, Bottle, hook, redirect, abort
import beaker.middleware
from search import Search, SearchResult
import simpleyaml
import StringIO
import parser
import getopt
import json

YASH_HOME = None
TEMPLATE_PATH = [os.path.join(os.getcwd(), "views")]
YASH_DATA_HOME = os.path.expanduser("~/.yash")
SUPPORTED_PLAIN_FILE_TYPES = ["markdown", "md", "txt", "plan", "py", "org"]
COMPOSITE_PLAN_NAME = "__summary__.plan.md"
COMPOSITE_PLAN_TITLE = u"_总计划_"

session_opts = {
    'session.type': 'file',
    'session.data_dir': YASH_DATA_HOME + '/session/',
    'session.auto': True,
}

app = beaker.middleware.SessionMiddleware(bottle.app(), session_opts)

class ProjectWrapper(parser.Project):
    def __init__(self, delegate_projects):
        self.delegate_projects = delegate_projects

        project_start_dates = [project.project_start_date for project in delegate_projects]
        min_project_start_date = min(project_start_dates)
        _, min_project_start_date = parser.skip_weekend(min_project_start_date)
        self.project_start_date = min_project_start_date

        self.tasks = []
        self.vacations = {}
        for project in delegate_projects:

            # calculate margin
            margin = parser.calculate_date_delta_skip_weekend(
                min_project_start_date,
                project.project_start_date
            )

            for user, vacation_list in project.vacations.iteritems():
                if not user in self.vacations:
                    self.vacations[user] = []

                self.vacations[user].extend(vacation_list)

            for task in project.tasks:
                task.start_point += margin
                self.tasks.append(task)

        # sort the tasks
        self.tasks = sorted(self.tasks, key = lambda task : task.start_point)

        # mans
        mans = set([])
        for project in delegate_projects:
            mans = mans.union(project.mans)

        self.mans = list(mans)

class User:
    def __init__(self, username, role):
        self.username = username
        self.role = role

class Config:
    def __init__(self):
        config = simpleyaml.safe_load(open(YASH_DATA_HOME + "/config.yaml"))
        self.roles = config["roles"]
        self.users = config["users"]

    def authenticate(self, username, password):
        return username in self.users and self.users[username]['password'] == password

    def get_role_by_username(self, username):
        user = self.users[username]
        if not user:
            return "public"
        else:
            return user["role"]

    def has_right(self, role, path):
        """Whether the specified `role` has the right to
           access the specified path?
        """

        for p in self.roles[role]:
            if re.compile(p).match(path):
                return True

        return False

    def is_login_required(self, url):
        staticFilePattren = '^/static/'
        return (not url in ["/login", "/not-authorized", "/logout", "/"]) and not re.search(staticFilePattren, url)

config = Config()

def session_get(key):
    session = bottle.request.environ.get('beaker.session')
    return session.get(key)

def session_set(key, value):
    session = bottle.request.environ.get('beaker.session')

    session[key] = value
    session.save()

def session_get_role():
    user = session_get("user")
    if not user:
        return "public"
    return user.role

def post_get(name, default=''):
    return bottle.request.POST.get(name, default).strip()

def is_logined():
    return not session_get("user") is None

@hook('before_request')
def auth_hook():
    # role based authentication
    if not config.is_login_required(request.path):
        return

    role = session_get_role()
    # super user can access anything
    if role == "root":
        return

    if config.has_right(role, request.path):
        if role == "public" or is_logined():
            return
        else:
            redirect("/login")

    redirect("/not-authorized")

@get("/not-authorized")
def not_authorized():
    return "Access Denied!"

def common_view_args():
    return dict(request = request, is_logined = is_logined())

@get("/login")
@view("login")
def login():
    return dict()

@post("/login")
def login_post():
    username = request.forms.get("username")
    password = request.forms.get("password")

    if config.authenticate(username, password):
        role = config.get_role_by_username(username)
        user = User(username, role)
        session_set("user", user)
        redirect("/")
    else:
        redirect("/login")

@get("/logout")
def logout():
    session_set("user", None)
    redirect("/")

@get('/<filename:re:static\/.*\.(css|js|png|jpg|gif|ico|woff|woff2|ttf|map)>')
def static_files(filename):
    return static_file(filename, root=YASH_HOME + "/")

@get('/<filename:re:.*\.(png|jpg|gif|ico|html|js|css)>')
def images(filename):
    return static_file(filename, root = os.getcwd())

@route('/search')
@view('search')
def search_files():
    keyword = request.GET.get('w')
    if len(keyword) > 0:
        keyword = keyword.strip()

    s = Search(os.getcwd(), keyword.decode("utf-8"), ("*.markdown", "*.md"))
    result = s.walk()

    result = [x for x in result if x.items is not None]
    newresult = []
    for x in result:
        x = SearchResult(x.fullpath[len(os.getcwd()):len(x.fullpath)], x.items)
        x.name = extract_file_title_by_fullurl(x.fullpath)

        newresult.append(x)

    return dict(results = newresult, keyword = keyword, request = request, is_logined = is_logined())

def render_markdown(text):
    return markdown.markdown(
        text,
        extras        = ["tables", "code-friendly", "fenced-code-blocks"]
    )

def markdown_files_1(text, fullurl):
    html = render_markdown(text)

    breadcrumbs = calculate_breadcrumbs(fullurl)
    title = extract_file_title_by_fullurl(fullurl)
    return dict(html = html,
                request = request,
                title = title,
                is_logined = is_logined(),
                breadcrumbs = breadcrumbs)

def read_file_from_disk(fullpath):
    if not os.path.exists(fullpath):
        abort(404, "Nothing to see here, honey!")

    input_file = codecs.open(fullpath, mode="r", encoding="utf-8")
    text       = input_file.read()

    return text

def extract_file_title(fullpath):
    if not os.path.exists(fullpath) and os.path.basename(fullpath) == COMPOSITE_PLAN_NAME:
        name = COMPOSITE_PLAN_TITLE
    else:
        input_file = codecs.open(fullpath, mode="r", encoding="utf-8")
        name       = input_file.readline()
        name       = name.strip("#")
        input_file.close()

    return name

def extract_file_title_by_fullurl(fullurl):
    physical_path = os.getcwd() + fullurl
    name = os.path.basename(physical_path)
    is_dir = os.path.isdir(physical_path)
    if os.path.isdir(physical_path):
        namepath = physical_path + "/.name"
        if os.path.exists(namepath):
            name = extract_file_title(namepath)
    else:
        extension_idx = name.rfind(".")
        extension = name[extension_idx + 1:]
        if extension in SUPPORTED_PLAIN_FILE_TYPES:
            name = extract_file_title(physical_path)

    name = name.strip()
    if len(name) > 0:
        return name
    else:
        return os.path.basename(fullurl)

@get('/<filename:re:.*\.plan\.(md|markdown)>')
@view('gantt')
def serve_plan(filename):
    fullpath   = os.getcwd() + "/" + filename
    man = request.GET.get('man')

    if not os.path.exists(fullpath):
        basename = os.path.basename(fullpath)
        dirname = os.path.dirname(fullpath)
        if basename == COMPOSITE_PLAN_NAME and os.path.exists(dirname + "/" + ".plan"):
            plan_files = os.listdir(dirname)
            plan_files = [x for x in plan_files if x.endswith(".plan.md")]
            projects = []
            for plan in plan_files:
                fullpath = dirname + "/" + plan
                text = read_file_from_disk(fullpath)
                project = parser.parse(text)

                projects.append(project)

            project = ProjectWrapper(projects)
            raw_text = ""
            show_text = False
    else:
        text = read_file_from_disk(fullpath)
        project = parser.parse(text)
        raw_text = read_file_from_disk(fullpath)
        raw_text = render_markdown(raw_text)
        show_text = True

    # make project info to json
    texts = []
    for idx, task in enumerate(project.tasks):
        # if not man or man == task.man.encode("utf-8"):
        taskjson = {}
        taskjson["taskName"] = render_markdown(task.name.encode("utf-8"))
        taskjson["cleanedTaskName"] = task.name.encode("utf-8")
        taskjson["owner"] = task.man.encode("utf-8")
        taskjson["cost"] = task.man_day
        taskjson["start"] = str(project.task_start_date(task))
        taskjson["end"] = str(project.task_end_date(task))
        taskjson["isDelayed"] = str(project.is_delayed(task))
        taskjson["progress"] = str(task.status)
        texts.append(taskjson)

    html = json.dumps(texts)
    fullurl = "/" + filename
    title = extract_file_title_by_fullurl(fullurl)
    breadcrumbs = calculate_breadcrumbs(fullurl)
    man_stats = pretty_print_man_stats(project.tasks)

    # render the raw text
    fullpath   = os.getcwd() + "/" + filename

    return dict(html = html,
                title = title,
                project = project,
                man_stats = man_stats,
                selected_man = man,
                raw_text = raw_text,
                breadcrumbs = breadcrumbs, request = request, is_logined = is_logined())

def pretty_print_man_stats(tasks):
    man2days = {}
    for task in tasks:
        if not man2days.get(task.man):
            man2days[task.man] = [0,0] # finished_man_days, total_man_days

        task_status = task.status
        man_days = task.man_day

        finished_man_days = task_status * man_days / 100
        man2days[task.man][0] = man2days[task.man][0] + finished_man_days
        man2days[task.man][1] = man2days[task.man][1] + man_days

    return man2days


@route('/<filename:re:.*\.xml>')
def xml_files(filename):
    fullpath   = os.getcwd() + "/" + filename
    text = read_file_from_disk(fullpath)
    response.content_type = "text/xml"
    return text

@route('/<filename:re:.*\.sql>')
@view("code")
def sql_files(filename):
    fullpath   = os.getcwd() + "/" + filename
    text = read_file_from_disk(fullpath)
    mode = "text"
    if filename.endswith(".sql"):
        mode = "sql"
    response.content_type = "text/html"
    breadcrumbs = calculate_breadcrumbs("/" + filename)
    title = extract_file_title_by_fullurl("/" + filename)
    return dict(mode = mode,
                code = text,
                request = request,
                title = title,
                breadcrumbs = breadcrumbs,
                is_logined = is_logined()
    )

@route('/<filename:re:.*\.(txt|properties|py|org)>')
def plain_files(filename):
    fullpath   = os.getcwd() + "/" + filename
    text = read_file_from_disk(fullpath)

    response.content_type = "text/plain"
    return text

@route('/<filename:re:.*\.(md|markdown)>')
@view('markdown')
def markdown_files(filename):
    fullpath   = os.getcwd() + "/" + filename
    text = read_file_from_disk(fullpath)

    return markdown_files_1(text, "/" + filename)

class FileItem:
    def __init__(self, name, path, is_dir):
        self.name = name
        self.path = path
        self.is_dir = is_dir

def calculate_breadcrumbs(path):
    path = path.strip("/")
    if len(path) == 0:
        paths = []
    else:
        paths = path.split("/")

    ret = [FileItem("Yash", "/", False)]
    totalpath = ""
    for p in paths:
        realpath = totalpath + "/" + p
        name = extract_file_title_by_fullurl(realpath)
        ret.append(FileItem(name, realpath, False))
        totalpath = realpath

    return ret

@route('/<filename:re:.*>')
@view('directory')
def directories(filename):
    physical_path = os.getcwd() + "/" + filename
    if len(filename) == 0:
        fullurl = ""
    else:
        fullurl = "/" + filename

    if not os.path.exists(physical_path):
        abort(404, "Nothing to see here, Honey!")

    files = os.listdir(physical_path)

    # check whether it contains a .plan file
    contains_plan_flag = ".plan" in files

    files = [x for x in files if not x.startswith(".")]
    role = session_get_role()
    if fullurl == "" and not role == "root":
        files = [x for x in files if config.has_right(role, fullurl + "/" + x)]

    filemap = []
    for f in files:
        newfullurl = fullurl + "/" + f
        name = extract_file_title_by_fullurl(newfullurl)
        is_dir = os.path.isdir(physical_path + "/" + f)
        filemap.append(FileItem(name, newfullurl, is_dir))

    if contains_plan_flag:
        filemap.append(FileItem(COMPOSITE_PLAN_TITLE, fullurl + "/" + COMPOSITE_PLAN_NAME, False))

    breadcrumbs = calculate_breadcrumbs(fullurl)
    title = extract_file_title_by_fullurl(fullurl)
    return dict(files = filemap,
                fullurl = fullurl,
                title = title,
                breadcrumbs = breadcrumbs,
                request = request,
                is_logined = is_logined())

if __name__ == '__main__':
    opts, args = getopt.getopt(sys.argv[1:], 'p:h')

    port = 80
    for opt_name, opt_value in opts:
        opt_value = opt_value.strip()
        if opt_name == '-p':
            port = int(opt_value)
        if opt_name == '-h':
            print """Usage: yash.py -p <port>"""

    YASH_HOME = sys.path[0]
    bottle.TEMPLATE_PATH = [os.path.join(YASH_HOME, "views")]
    bottle.run(app = app, host='0.0.0.0', port=port)
