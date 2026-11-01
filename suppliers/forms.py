from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'contact_person', 'phone', 'alt_phone', 'email', 'gstin', 'address', 'city', 'state', 'notes', 'is_active']

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        clash = Supplier.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A supplier with this name already exists.')
        return name

    def clean_gstin(self):
        return self.cleaned_data['gstin'].strip().upper()
