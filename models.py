"""User model for Flask-Login."""

from flask_login import UserMixin
from db import get_user_by_id


class User(UserMixin):
    """Flask-Login user model backed by the database."""

    def __init__(self, id, username, email, is_active=True):
        self.id = id
        self.username = username
        self.email = email
        self._is_active = is_active

    @property
    def is_active(self):
        return self._is_active

    @staticmethod
    def get(user_id):
        """Load a user from the database by id."""
        row = get_user_by_id(int(user_id))
        if row:
            return User(
                id=row["id"],
                username=row["username"],
                email=row["email"],
                is_active=row["is_active"],
            )
        return None
