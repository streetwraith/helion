from django.db import models
from django.conf import settings

class Character(models.Model):
    character_id = models.IntegerField(primary_key=True)
    character_name = models.CharField(max_length=128)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text="The user to whom this token belongs."
    )
    def __str__(self):
        return str(self.character_id) + ' ' + self.character_name