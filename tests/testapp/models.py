"""A minimal host: tenants, roles holding permission codenames, members, and a record to change."""

from django.conf import settings
from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Role(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    permissions = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name


class Member(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="member")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="members")
    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.SET_NULL)
    removed = models.BooleanField(default=False)


class Document(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    folder = models.CharField(max_length=50, default="general")
    title = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    published = models.BooleanField(default=False)

    def __str__(self):
        return self.title
