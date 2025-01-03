from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.utils import timezone

class Customer(models.Model):
    name = models.CharField(max_length=255, verbose_name="اسم الزبون")
    email = models.EmailField(blank=True, null=True, verbose_name="البريد الإلكتروني")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="رقم الهاتف")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="تاريخ الإضافة")
    
    class Meta:
        verbose_name = "زبون"
        verbose_name_plural = "الزبائن"
        ordering = ['-created_at']

    def __str__(self):
        return self.name
    
    def get_total_purchases(self):
        return sum(invoice.get_total() for invoice in self.invoice_set.all())

class Product(models.Model):
    name = models.CharField(max_length=100, verbose_name="اسم المنتج")
    description = models.TextField(blank=True, null=True, verbose_name="وصف المنتج")
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="سعر الشراء")
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="سعر البيع")
    quantity = models.IntegerField(default=0, verbose_name="الكمية")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإضافة")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ التحديث")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "المنتج"
        verbose_name_plural = "المنتجات"
        ordering = ['name']

class Invoice(models.Model):
    PAYMENT_STATUS = (
        ('pending', 'معلق'),
        ('partial', 'مدفوع جزئياً'),
        ('paid', 'مدفوع بالكامل'),
    )
    
    customer = models.ForeignKey(
        Customer, 
        on_delete=models.PROTECT,  # Prevent deleting customers with invoices
        verbose_name="الزبون"
    )
    date = models.DateTimeField(default=timezone.now, verbose_name="تاريخ الفاتورة")
    status = models.CharField(
        max_length=10,
        choices=PAYMENT_STATUS,
        default='pending',
        verbose_name="حالة الدفع"
    )
    tax_exempt = models.BooleanField(default=False, verbose_name="معفى من الضريبة")
    notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات")

    class Meta:
        verbose_name = "فاتورة"
        verbose_name_plural = "الفواتير"
        ordering = ['-date']

    def __str__(self):
        return f"فاتورة #{self.id} - {self.customer.name}"
    
    def get_subtotal(self):
        return sum(item.get_subtotal() for item in self.items.all())
    
    def get_tax_amount(self):
        from django.apps import apps
        Settings = apps.get_model('sales_app', 'Settings')
        settings = Settings.objects.first()
        if not settings:
            return Decimal('0.00')
        tax_rate = settings.tax_rate / Decimal('100.00')
        return self.get_subtotal() * tax_rate
    
    def get_total(self):
        """حساب المجموع الكلي للفاتورة"""
        if self.tax_exempt:
            return Decimal('0.00')
            
        subtotal = sum(item.get_subtotal() for item in self.items.all())
        if not self.tax_exempt:
            settings = Settings.objects.first()
            if settings and settings.tax_rate:
                tax_amount = subtotal * (settings.tax_rate / 100)
                return subtotal + tax_amount
        return subtotal
    
    def get_remaining_amount(self):
        paid = sum(payment.amount for payment in self.payments.all())
        return self.get_total() - paid

    @property
    def is_paid(self):
        total_payments = self.payments.aggregate(total=models.Sum('amount'))['total'] or 0
        return total_payments >= self.get_total()

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="الفاتورة"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,  # Prevent deleting products that are in invoices
        verbose_name="المنتج"
    )
    quantity = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="الكمية"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="السعر"
    )

    class Meta:
        verbose_name = "عنصر الفاتورة"
        verbose_name_plural = "عناصر الفاتورة"

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"
    
    def get_subtotal(self):
        return self.quantity * self.price

class Payment(models.Model):
    PAYMENT_METHODS = (
        ('cash', 'نقداً'),
        ('card', 'بطاقة ائتمان'),
        ('transfer', 'تحويل بنكي'),
        ('check', 'شيك'),
    )
    
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT, 
        null=True, 
        blank=True, 
        related_name='payments',
        verbose_name="الفاتورة"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='payments',
        null=True,
        blank=True,
        verbose_name="الزبون"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="المبلغ"
    )
    date = models.DateTimeField(default=timezone.now, verbose_name="تاريخ الدفع")
    payment_method = models.CharField(
        max_length=10,
        choices=PAYMENT_METHODS,
        default='cash',
        verbose_name="طريقة الدفع"
    )
    reference = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="رقم المرجع"
    )

    class Meta:
        verbose_name = "دفعة"
        verbose_name_plural = "الدفعات"
        ordering = ['-date']

    def __str__(self):
        return f"دفعة #{self.id} - {self.invoice.customer.name if self.invoice else 'بدون فاتورة'}"

class Settings(models.Model):
    company_name = models.CharField(max_length=255, verbose_name="اسم الشركة")
    company_address = models.TextField(blank=True, null=True, verbose_name="عنوان الشركة")
    company_phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="هاتف الشركة")
    company_email = models.EmailField(blank=True, null=True, verbose_name="البريد الإلكتروني للشركة")
    tax_number = models.CharField(max_length=50, blank=True, null=True, verbose_name="الرقم الضريبي")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15.0, verbose_name="نسبة الضريبة")
    currency = models.CharField(max_length=3, default='SAR', verbose_name="العملة")
    invoice_notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات الفاتورة الافتراضية")
    low_stock_notifications = models.BooleanField(default=True, verbose_name="تنبيهات المخزون المنخفض")
    payment_notifications = models.BooleanField(default=True, verbose_name="تنبيهات المدفوعات")
    invoice_notifications = models.BooleanField(default=True, verbose_name="تنبيهات الفواتير")
    last_backup_date = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ آخر نسخة احتياطية")

    class Meta:
        verbose_name = "الإعدادات"
        verbose_name_plural = "الإعدادات"

    @classmethod
    def get_settings(cls):
        settings = cls.objects.first()
        if not settings:
            settings = cls.objects.create()
        return settings

class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('invoice', 'فاتورة جديدة'),
        ('payment', 'دفعة جديدة'),
        ('low_stock', 'تنبيه المخزون'),
        ('system', 'إشعار النظام'),
    ]
    
    title = models.CharField(max_length=200, verbose_name="العنوان")
    message = models.TextField(verbose_name="الرسالة")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, verbose_name="نوع الإشعار")
    is_read = models.BooleanField(default=False, verbose_name="مقروء")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "إشعار"
        verbose_name_plural = "الإشعارات"
    
    def __str__(self):
        return self.title