from django.db import models

class EmailLog(models.Model):
    to = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=[("queued","Queued"),("sent","Sent"),("failed","Failed")], default="queued")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["to", "created_at"])]

    def __str__(self):
        return f"{self.to} [{self.status}] {self.subject}"
