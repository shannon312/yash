#-*-encoding: utf-8 -*-
import sys
import re
import datetime
from math import ceil

TASK_LINE_PATTERN = "\*(.+)\-\-\s*([0-9]+\.?[0-9]?)\s*(\[(.+?)\])?(\[([0-9]+)%\s*\])?\s*$"
HEADER_PATTERN = "^(#{2,})(.*)"
VACATION_PATTERN = "\*(.+)\-\-\s*([0-9]{4}\-[0-9]{2}\-[0-9]{2})(\s*\-\s*([0-9]{4}\-[0-9]{2}\-[0-9]{2}))?\s*$"
PROJECT_START_DATE_PATTERN = 'ProjectStartDate\:\s*([0-9]{4}\-[0-9]{2}\-[0-9]{2})'
THE_ALL_MAN = "__ALL__"

class ParserException(Exception):
    pass

class Options:
    def __init__(self):
        self.print_man_stats = False
        self.only_nonstarted = False
        self.english = False
        self.man = None

class Project:
    def __init__(self, project_start_date, tasks, vacations):
        self.project_start_date = project_start_date
        self.tasks = tasks
        self.vacations = vacations

        self.mans = []
        self.status = 0
        self.total_man_days = 0
        self.cost_man_days = 0
        self.init_status()

    def task_start_date(self, task):
        return add_days(self.project_start_date, task.start_point, task.man, self.vacations)

    def task_end_date(self, task):
        return add_days(self.project_start_date, task.start_point + task.man_day, task.man, self.vacations, False)

    def is_delayed(self, task):
        return task.status < 100 and self.task_end_date(task) < datetime.datetime.now().date()

    def init_status(self):
        # calculate all the mans
        for task in self.tasks:
            if not task.man in self.mans:
                self.mans.append(task.man)

        # handle the __ALL__ vacations
        if THE_ALL_MAN in self.vacations:
            for man in self.mans:
                if not man in self.vacations:
                    self.vacations[man] = []

                self.vacations[man].extend(self.vacations[THE_ALL_MAN])
            del self.vacations[THE_ALL_MAN]

        total_man_days = 0
        cost_man_days = 0
        # calculate the start_date, end_date of all tasks
        for task in self.tasks:
            total_man_days += task.man_day
            cost_man_days += task.man_day * task.status / 100
            task.start_date = self.task_start_date(task)
            task.end_date = self.task_end_date(task)

        project_status = 0
        if total_man_days > 0:
            project_status = cost_man_days / total_man_days

        self.total_man_days = total_man_days
        self.cost_man_days = cost_man_days
        self.status = project_status

EmptyProject = Project(datetime.datetime.now().date(), [], {})

class Task:
    def __init__(self, name, man_day, man, status=0):
        """
        Arguments:
        - `self`:
        - `name`:
        - `man_day`
        """
        self.name = name
        self.man_day = man_day
        self.man = man
        self.status = int(status)
        self.start_point = None
        self.start_date = None
        self.end_date = None

def is_weekend(date1):
    weekday = date1.isoweekday()
    return weekday > 5

def skip_weekend(date1):
    if is_weekend(date1):
        padding_days = (7 - date1.isoweekday()) + 1
        date1 = date1 + datetime.timedelta(days=padding_days)
        return True, date1
    else:
        return False, date1


def skip_vacation(man, date1, vacations):
    if vacations.get(man) and vacations.get(man).count(str(date1)) > 0:
        date1 = date1 + datetime.timedelta(days=1)
        return True, date1
    else:
        return False, date1

def skip_weekend_or_vacation(date1, man = None, vacations = {}):
    while True:
        skipped, date1 = skip_weekend(date1)

        if not man == None:
            skipped, date1 = skip_vacation(man, date1, vacations)

        if not skipped:
            break

    return date1

def add_days(curr_day, days, man = None, vacations = {}, is_start_date = True):
    idx = int(ceil(days))
    if idx > days:
        idx -= 1
    else:
        if not is_start_date:
            idx -= 1

    ret = curr_day
    # current day may be a weekend day, so we skip the weekend first
    ret = skip_weekend_or_vacation(ret, man, vacations)

    while idx > 0:
        ret = ret + datetime.timedelta(days=1)
        # skip the weekend and vacations
        ret = skip_weekend_or_vacation(ret, man, vacations)
        idx -= 1

    return ret

def calculate_date_delta_skip_weekend(date1, date2):
    """
    计算两个日期之间相差的天数，但是会跳过周末。
    """

    ret = date2
    cnt = 0

    # 如果项目开始日期是周末(其实不应该这样，为什么项目是周末开始)，
    # 那么自动往后推一天, 推到下周一再开始这个项目
    if is_weekend(ret):
        cnt += 1

    while ret > date1:
        if not is_weekend(ret):
            cnt += 1
        ret = ret - datetime.timedelta(days = 1)

    return cnt

def schedule(tasks):
    curr_days = {}
    id_to_start_point = {}
    for task in tasks:
        if not curr_days.get(task.man):
            curr_days[task.man] = 0
        task.start_point = curr_days[task.man]
        curr_days[task.man] += task.man_day


def parse_date(input):
    return datetime.datetime.strptime(input, '%Y-%m-%d').date()

def get_headers_as_str(headers):
    return "-".join([header for [_, header] in headers])

def parse_header_line(curr_headers, m):
    new_header_level = len(m.group(1).strip())
    new_header = m.group(2).strip()
    for i in range(len(curr_headers)):
        header_level, header = curr_headers[len(curr_headers) - 1 - i]
        if new_header_level <= header_level:
            curr_headers.pop()

    curr_headers.append([new_header_level, new_header])

def parse_task_line(tasks, curr_headers, m):
    task_name = m.group(1).strip()
    if len(curr_headers) > 0:
        task_name = get_headers_as_str(curr_headers) + "-" + task_name

    man_day = m.group(2).strip()
    man_day = float(man_day)
    man = m.group(4)
    if man:
        man = man.strip()
    else:
        man = "TODO"

    status = 0
    if m.group(6):
        status = m.group(6).strip()

    task = Task(task_name, man_day, man, status)
    tasks.append(task)

def parse_vacation_line(vacations, m):
    man = m.group(1).strip()
    vacation_date = parse_date(m.group(2).strip())
    vacation_date_end = vacation_date
    if m.group(4):
        vacation_date_end = parse_date(m.group(4).strip())

    if not vacations.get(man):
        vacations[man] = []

    xdate = vacation_date
    while xdate <= vacation_date_end:
        vacations[man].append(str(xdate))
        xdate += datetime.timedelta(days=1)

def parse(content):
    lines = content.split('\n')
    tasks = []
    vacations = {}

    project_start_date = None
    curr_headers = []
    for line in lines:

        # parse task line
        m = re.search(TASK_LINE_PATTERN, line)
        if m:
            parse_task_line(tasks, curr_headers, m)
            continue

        # parse vacation line
        m = re.search(VACATION_PATTERN, line)
        if m:
            parse_vacation_line(vacations, m)
            continue

        # parse project_start_date line
        m = re.search(PROJECT_START_DATE_PATTERN, line)
        if m and m.group(1):
            project_start_date = parse_date(m.group(1).strip())
            continue

        # parse header line
        m = re.search(HEADER_PATTERN, line)
        if m:
            parse_header_line(curr_headers, m)

    if not project_start_date:
        raise ParserException("Please specify the project start date!")

    schedule(tasks)

    return Project(project_start_date, tasks, vacations)
