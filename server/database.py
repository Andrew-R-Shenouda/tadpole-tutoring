import copy
import dataset
from datetime import datetime, timedelta
import validators
import secrets
import string
from typing import *

from utils.log import *
from config import DB


class Database:
    """A class used to interface with the database storing teachers, students, tutoring times, and who claimed them

    To store this data, we use a sql database with 3 tables:
        1) "``teachers``": A table used to map the teachers email address to their name. Columns are as follows:
            - "``email``": The teacher's email address
            - "``first_name``": The teacher's first name
            - "``last_name``": The teacher's last name
            - "``subjects``": A pipe separated list of subjects that the teacher teachers
        2) "``students``": A table used to map a student's email address to their name. Columns are as follows:
            - "``email``": The student's email address
            - "``first_name``": The student's first name
            - "``last_name``": The student's last name
            - "``notes``": Notes about the student by the teacher
        3) "``times``": A table used to store the times teachers have set for tutoring, and who, if anyone, has claimed them. Columns are as follows:
            - "``teacher_email``": The teacher's email address hosting the tutoring session
            - "``start_time``": A unix timestamp representing the start of the session
            - "``duration_type``": A number representing the duration of the session (0 for 1hr, 1 for 1.5hr)
            - "``claimed``": A boolean representing if the session has been claimed
            - "``student``": The email address of the student who claimed the session
        4) "``auth`": A table used to store the email and auth, key-value pairs. Columns are as follows: 
            - "``teacher_email``": The teacher's email address hosting the tutoring session.
            - "``token``: The random 128-bit token hex string.

    Examples:
        **Handle a teacher login (call this once you know the teacher's email address)**::

            db.init_db_connection()
            if not db.check_teacher("ateacher@choate.edu"):
                # Teacher isn't in the database
                # Get the teacher to specify their name and the subjects they teach, then call the following:
                db.add_teacher("ateacher@choate.edu", "A", "Teacher", ["Math", "English"])
            db.end_db_connection()

        **Handle a student login**::

            db.init_db_connection()
            db.add_student("astudent@choate.edu", "A", "Student")
            db.end_db_connection()

        **Add a tutoring time (from teacher acct)**::

            db.init_db_connection()
            # 0 at the end means is is a 1hr session, 1 would be 1.5 hr
            db.add_time_for_tutoring("ateacher@choate.edu", datetime.datetime(2020, 4, 14, 10, 00), 0)
            db.end_db_connection()

        **Claim an available math tutoring time (from student acct)**::

            db.init_db_connection()
            possible_times = db.search_times(subject="Math", min_start_time=datetime.datetime.now(), must_be_unclaimed=True)
            db.claim_time("astudent@choate.edu", time_id=possible_times[0]['id'])
            db.end_db_connection()

        **Get student notes (from teacher acct)**::

            db.init_db_connection()
            notes = db.get_student_notes("astudent@choate.edu")
            db.end_db_connection()

        **Change student notes (from teacher acct)**::

            db.init_db_connection()
            db.set_student_notes("astudent@choate.edu", "A Student is a very good student...")
            db.end_db_connection()
    """

    def __init__(self):
        self._db = None
        self.init_db_connection()
        self.end_db_connection()

    def check_student(self, email: str) -> bool:
        """
        Checks if a student is in the database

        Args:
            email: The email of the student

        Returns:
            True if the student was added to the database or was already there, False if something went wrong
        """
        return bool(self._db['students'].find_one(email=email))

    def check_teacher(self, email: str) -> bool:
        """
        Checks if a teacher is in the database
        
        Args:
            email: The email of the teacher

        Returns:
            True if the teacher was added to the database or was already there, False if something went wrong
        """
        return bool(self._db['teachers'].find_one(email=email))

    def add_teacher(self, email: str, first_name: str, last_name: str, subjects: List[str]) -> bool:
        """
        Adds a teacher to the database

        Args:
            email: The email of the teacher
            first_name: The teacher's first name
            last_name: The teacher's last name

        Returns:
            True if the teacher was added to the database or was already there, False if something went wrong
        """
        data = {"email": email, "first_name": first_name, "last_name": last_name, "subjects": "|".join(subjects)}
        return self._transactional_upsert("teachers", data, ["email"])

    def add_student(self, email: str) -> bool:
    # def add_student(self, email: str, first_name: str, last_name: str) -> bool:
        """
        Adds a student to the database

        Args:
            email: The email of the student
            first_name: The student's first name
            last_name: The student's last name
        """
        data = {"email": email}
        return self._transactional_upsert("students", data, ["email"])

    def make_teacher(self, email: str, subjects: List[str]) -> bool:
        """
        Makes a student into a teacher

        Args:
            email: The email of the student

        Returns:
            True if the
        """

        if student := self.get_student(email):
            if student_email := student.get("email"):
                if self.add_teacher(student_email, student.get("first_name"), student.get("last_name"), subjects):
                    self._db['students'].delete(email=student_email)
                    return True

        return False

    def add_time_for_tutoring(self, teacher_email: str, start_time: datetime, duration_type: int = 0):
        """
        Adds a time for tutoring. Intended to be used by a teacher once they have logged in. It is assumed that they
        are already authorized.

        Args:
            teacher_email: Teacher's email address
            start_time: Start time of the session
            is_hour_long: If the session is 1hr long (otherwise 1.5hr)

        Returns:
            False if there was already a session in that time or the insert failed, otherwise True
        """
        start_time_unix = start_time.timestamp()

        data = {'teacher_email': teacher_email,
                'start_time': start_time_unix,
                'duration_type': duration_type,
                'claimed': False,
                'student': ''}

        return self._transactional_insert("times", data)

    def claim_time(self, student_email: str, time_id: int):
        """
        Claim a time in the database. Intended to be used by a student once they have logged in. It is assumed that they
        are already authorized.

        Args:
            student_email: The email of the student claiming the time
            time_id: The id of the time ('id' key in the dict)

        Returns:
            False if the time was already claimed or there wasn't a time with the specified id, True if the time was successfully claimed
        """
        time_to_claim: dict = self._db['times'].find_one(id=time_id)

        if time_to_claim:
            if time_to_claim.get('claimed'):
                log_info("Time with id " + str(time_id) + " is already claimed", header=student_email)
                return False

            time_to_claim['claimed'] = True
            time_to_claim['student'] = student_email

            return self._transactional_upsert('times', time_to_claim, ["id"])

        log_info("Unable to find time with id " + str(time_id), header=student_email)
        return False

    def unclaim_time(self, student_email: str, time_id: int):
        """
        Unclaim a time in the database. Intended to be used by a student once they have logged in. It is assumed that they
        are already authorized.

        Args:
            student_email: The email of the student claiming the time
            time_id: The id of the time ('id' key in the dict)

        Returns:
            False if the time was claimed by someone else or there wasn't a time with the specified id, True if the time was successfully unclaimed
        """
        time_to_unclaim: dict = self._db['times'].find_one(id=time_id)

        if time_to_unclaim:
            if not time_to_unclaim.get('claimed'):
                log_info("Attempted to unclaim an unclaimed time with id " + str(time_id), header=student_email)
                return False

            c_student = time_to_unclaim.get("student")

            if c_student and c_student != student_email:
                log_info("Attempted to unclaim time id " + str(time_id) + " that belongs to " + str(c_student), header=student_email)
                return False

            time_to_unclaim['claimed'] = False

            return self._transactional_upsert('times', time_to_unclaim, ["id"])

        log_info("Unable to find time with id " + str(time_id), header=student_email)
        return False

    def search_times(self, teacher_email: str = None, subject: str = None, min_start_time: datetime = None, max_start_time: datetime = None, must_be_unclaimed: bool = False) -> List[dict]:
        """
        Searches the database for tutoring sessions satisfying the search parameters

        Args:
            teacher_email: The teacher's email address (None for all teachers)
            subject: The subject of the time (None for all subjects)
            min_start_time: The earliest start time for the session (None for all times)
            max_start_time: The latest start time for the session (None for all times)
            must_be_unclaimed: If the session has to be unclaimed

        Returns a list of dicts representing each time with the following keys:
                - "``teacher_email``": ``str`` - The email of the teacher hosting the session
                - "``start_time``": ``datetime`` - The start time of the session
                - "``end_time``": ``datetime`` - The end time of the session
                - "``duration``": ``timedelta`` - The start time of the session
                - "``claimed``": ``bool`` - If the session has been claimed
                - "``student``": ``str`` - The student who claimed the session (if any)

        Returns:
            The list of dicts
        """
        if teacher_email:
            possible_times = self._db['times'].find(teacher_email=teacher_email)
        else:
            possible_times = self._db['times'].all()

        results: List[dict] = []

        for t in possible_times:
            try:
                c_start = t['start_time']
                c_claimed = t['claimed']
                c_teacher_email = t['teacher_email']
            except KeyError:
                log_error("Invalid time: " + str(t))
                continue

            if subject:
                if c_teacher := self._db['teachers'].find_one(email=c_teacher_email):
                    if subject not in c_teacher['subjects']:
                        continue

            if must_be_unclaimed and c_claimed:
                continue

            if min_start_time and c_start < min_start_time.timestamp():
                continue

            if max_start_time and c_start > max_start_time.timestamp():
                continue

            t['start_time'] = datetime.fromtimestamp(c_start)
            results.append(t)

        return results

    def get_student_notes(self, student_email: str) -> str:
        """
        Gets the teacher notes for a given student

        Args:
            student_email: The email of the student

        Returns:
            The notes as a string. Returns an empty string if no student was found or if the student has no notes
        """
        if student := self._db['students'].find_one(email=student_email):
            if notes := student.get('notes'):
                return notes

        return ''

    def get_student(self, student_email: str) -> dict:
        """
        Gets everything for a given student

        Args:
            student_email: The email of the student

        Returns:
            Everything about the student as a dict. Returns an empty dict if no student was found or if the student has no notes.
        """
        if student := self._db['students'].find_one(email=student_email):
            return student
        return {}

    def set_student_notes(self, student_email: str, notes: str) -> bool:
        """
        Sets the teacher notes for a given student

        Args:
            student_email: The email of the student
            notes: The notes to set

        Returns:
            True if the student is in the database and the notes were set, otherwise False
        """
        if student := self._db['students'].find_one(email=student_email):
            student['notes'] = notes

            return self._transactional_upsert('students', student, ['id'])

        return False

    def init_db_connection(self, attempt=0):
        """
        Initializes the database connection.

        Args:
            attempt: The attempt number to initialize the database connection (for recursion)
        """
        try:
            self._db = dataset.connect(DB, engine_kwargs={'pool_recycle': 3600, 'pool_pre_ping': True})
            # log_info("New Database Connection")
        except ConnectionResetError as e:
            log_info("ConnectionResetError " + str(e) + ", attempt: " + str(attempt))
            if attempt <= 3:
                self._db.close()
                self.init_db_connection(attempt=attempt + 1)
        except AttributeError as e:
            log_info("AttributeError " + str(e) + ", attempt: " + str(attempt))
            if attempt <= 3:
                self._db.close()
                self.init_db_connection(attempt=attempt + 1)

    def end_db_connection(self):
        """
        Closes the database connection
        """
        self._db.close()
        # log_info("Disconnected From Database")

    def _transactional_upsert(self, table: str, data: dict, key: list, attempt=0) -> bool:
        """
        Upserts a dict into a specified database table

        Args:
            table: The database table to upsert to
            data: The data to upsert
            key: The key to upsert with
            attempt: The attempt number (for recursion)

        Returns:
            True if the upsert succeeded, otherwise False
        """
        if attempt <= 3:
            self._db.begin()
            try:
                self._db[str(table)].upsert(dict(copy.deepcopy(data)), list(key))
                self._db.commit()
                return True
            except:
                self._db.rollback()
                log_info("Exception caught with DB, rolling back and trying again " + str((table, data, key, attempt)))
                return self._transactional_upsert(table, data, key, attempt=attempt + 1)
        else:
            log_info("Automatic re-trying failed with these args: " + str((table, data, key, attempt)))

        return False

    def _transactional_insert(self, table: str, data: dict, attempt=0) -> bool:
        """
        Inserts a dict into a specified database table

        Args:
            table: The database table to insert to
            data: The data to insert
            attempt: The attempt number (for recursion)

        Returns:
            True if the insert succeeded, otherwise False
        """
        if attempt <= 3:
            self._db.begin()
            try:
                self._db[str(table)].insert(dict(copy.deepcopy(data)))
                self._db.commit()
                return True
            except:
                self._db.rollback()
                log_info("Exception caught with DB, rolling back and trying again " + str((table, data, attempt)))
                return self._transactional_insert(table, data, attempt=attempt + 1)
        else:
            log_info("Automatic re-trying failed with these args: " + str((table, data, attempt)))

        return False

    def create_token(self, email: str) -> Any:
        """
        Creates or overwrites token if one exists. If creation was successful, return token. If not, return False.
        """

        token = secrets.token_hex(16)
        user = {"email": str(email), "token": token }
        if validators.email(email):
            if self._transactional_upsert("auth", user, ["email"]):
                return token
            else:
                log_info("Error creating token for " + str(email))
        else:
            log_info("Invalid email " + str(email))

        return False

    def check_auth_pair(self, token, email) -> Union[str, bool]:
        """
        Tries to fetch or make a token for a user. If not successful, return False
        """
        if token and email and self.possible_token(token):
            log_info("Checking token and email pair, " + str(token) + " "+ str(email))
            if expected_token := self.find_token_by_email(str(email)):
                if secrets.compare_digest(token, expected_token):
                    log_info("Token check success: " + str(token))
                    return True
        return False

    def find_token_by_email(self, email: str) -> Any:
        """
        Finds token by email. If the token does not exist, return False.
        """
        entry = self._db['auth'].find_one(email=str(email))
        token = entry.get('token')
        if token and self.possible_token(token):
            return token
        return False

    def possible_token(self, token: str) -> bool: 
        """
        Validates if the input is a valid 128-bit token (16 byte)

        Args:
            token: The possible 128-bit token

        Returns:
            True if it could be a token, otherwise False
        """
        try:
            if isinstance(int(str(token), 16), int) and all(c in string.hexdigits for c in str(token)):
                if len(str(token)) == 32:
                    if "/" not in token:  # extra validation
                        return True
        except:
            log_info("Impossible token: " + str(token))
            return False

        log_info("Impossible token: " + str(token))
        return False
