"""
Relationship intelligence engine — Phase 6.

Maintains a ContactProfile per (user_id, contact_email) pair.
"""


class RelationshipEngine:

    async def refresh_user(self, user_id: str) -> None:
        """
        TODO Phase 6: rebuild all contact profiles for a user from their
        email + meeting history.
        """
        raise NotImplementedError

    async def update_contact(self, user_id: str, email: dict) -> None:
        """
        TODO Phase 6: update a single contact's profile from a new email.
        Called inline during inbox sync.
        """
        raise NotImplementedError


relationship_engine = RelationshipEngine()
