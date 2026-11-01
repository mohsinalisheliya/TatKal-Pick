from django.db import models


class Supplier(models.Model):
    name = models.CharField(max_length=120, unique=True)
    contact_person = models.CharField(max_length=100, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    alt_phone = models.CharField(max_length=20, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    gstin = models.CharField('GST number', max_length=20, blank=True, default='')
    address = models.CharField(max_length=250, blank=True, default='')
    city = models.CharField(max_length=60, blank=True, default='')
    state = models.CharField(max_length=60, blank=True, default='')
    notes = models.CharField(max_length=300, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
