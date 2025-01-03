from django import forms
from .models import Customer, Product, Invoice, InvoiceItem, Payment
from django.forms import inlineformset_factory
from datetime import date

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'email', 'phone']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم الزبون'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'البريد الإلكتروني'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'رقم الهاتف'}),
        }

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'description', 'purchase_price', 'selling_price', 'quantity']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        purchase_price = cleaned_data.get('purchase_price')
        selling_price = cleaned_data.get('selling_price')
        
        if purchase_price and selling_price:
            if selling_price < purchase_price:
                raise forms.ValidationError('سعر البيع يجب أن يكون أكبر من سعر الشراء')
        return cleaned_data

class InvoiceForm(forms.ModelForm):
    tax_exempt = forms.BooleanField(
        label='معفى من الضريبة',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    date = forms.DateField(
        label='التاريخ',
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        }),
        initial=date.today
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        label='الزبون',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Invoice
        fields = ['customer', 'date', 'tax_exempt', 'notes']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['product', 'quantity', 'price']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
        }

    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        product = self.cleaned_data.get('product')
        
        if not self.instance.pk:  # جديد
            if product and quantity > product.quantity:
                raise forms.ValidationError("الكمية المطلوبة غير متوفرة في المخزون")
        else:  # تعديل
            if product and 'quantity' in self.changed_data:
                original_quantity = self.instance.quantity
                quantity_difference = quantity - original_quantity
                if quantity_difference > 0 and quantity_difference > product.quantity:
                    raise forms.ValidationError("الكمية المطلوبة غير متوفرة في المخزون")
        
        return quantity

InvoiceItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceItem,
    form=InvoiceItemForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=19,
    validate_max=True,
    fields=['product', 'quantity', 'price']
)

class PaymentForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        label='الزبون',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    date = forms.DateField(
        label='التاريخ',
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        }),
        initial=date.today
    )
    amount = forms.DecimalField(
        label='المبلغ',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'min': '0'
        })
    )
    payment_method = forms.ChoiceField(
        choices=Payment.PAYMENT_METHODS,
        label='طريقة الدفع',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Payment
        fields = ['customer', 'amount', 'payment_method', 'date']

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        invoice = self.cleaned_data.get('invoice')
        
        if invoice and amount > invoice.get_remaining_amount():
            raise forms.ValidationError("المبلغ المدخل أكبر من المبلغ المتبقي للفاتورة")
        return amount
