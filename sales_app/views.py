from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, F, Q
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models.deletion import ProtectedError
from .models import Customer, Product, Invoice, InvoiceItem, Payment, Settings, Notification
from .forms import CustomerForm, ProductForm, InvoiceForm, InvoiceItemForm, PaymentForm, InvoiceItemFormSet
from django.utils import timezone
import datetime
from django.views.decorators.http import require_http_methods
import qrcode
import base64
from io import BytesIO
from django.conf import settings
from decimal import Decimal, InvalidOperation
from django.http import JsonResponse, HttpResponse
import os
from django.core import management
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string, get_template
from django.core.exceptions import ValidationError
from xhtml2pdf import pisa

def customer_list(request):
    customers = Customer.objects.all().order_by('-created_at')
    # Pre-calculate total purchases for each customer
    for customer in customers:
        customer.total_purchases = customer.get_total_purchases()
    paginator = Paginator(customers, 10)
    page = request.GET.get('page')
    customers = paginator.get_page(page)
    return render(request, 'sales_app/customer_list.html', {'customers': customers})

def add_customer(request):
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, 'تم إضافة الزبون بنجاح')
            return redirect('sales_app:customer_detail', pk=customer.pk)
    else:
        form = CustomerForm()
    return render(request, 'sales_app/customer_form.html', {'form': form})

def edit_customer(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, 'تم تحديث بيانات الزبون بنجاح')
            return redirect('sales_app:customer_detail', pk=customer.pk)
    else:
        form = CustomerForm(instance=customer)
    return render(request, 'sales_app/customer_form.html', {'form': form, 'customer': customer})

def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    invoices = customer.invoice_set.all().order_by('-date')
    total_purchases = customer.get_total_purchases()
    return render(request, 'sales_app/customer_detail.html', {
        'customer': customer,
        'invoices': invoices,
        'total_purchases': total_purchases
    })

def customer_account_statement_print(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    invoices = customer.invoice_set.all().order_by('date')
    transactions = []
    balance = 0
    total_amount = 0  # Initialize total amount

    for invoice in invoices:
        total_amount += invoice.get_total()  # Calculate total amount
        balance += invoice.get_total()
        transactions.append({
            'date': invoice.date,
            'type': 'فاتورة',
            'reference': f'فاتورة #{invoice.id}',
            'debit': invoice.get_total(),
            'credit': 0,
            'status': 'مفتوحة',  # Example status, adjust as necessary
            'balance': balance
        })
        for payment in invoice.payments.all():
            balance -= payment.amount
            transactions.append({
                'date': payment.date,
                'type': 'دفعة',
                'reference': f'دفعة للفاتورة #{invoice.id}',
                'debit': 0,
                'credit': payment.amount,
                'status': 'مدفوعة',  # Example status, adjust as necessary
                'balance': balance
            })

    current_date = timezone.now().date()  # Get current date

    return render(request, 'sales_app/customer_account_statement_print.html', {
        'customer': customer,
        'transactions': transactions,
        'final_balance': balance,
        'total_amount': total_amount,
        'current_date': current_date
    })

def delete_customer(request, pk):
    if request.method == 'POST':
        customer = get_object_or_404(Customer, pk=pk)
        try:
            customer.delete()
            messages.success(request, 'تم حذف الزبون بنجاح')
        except ProtectedError:
            messages.error(request, 'لا يمكن حذف الزبون لوجود فواتير مرتبطة به')
    return redirect('sales_app:customer_list')

def product_list(request):
    search_query = request.GET.get('search', '')
    products = Product.objects.all()
    
    if search_query:
        products = products.filter(name__icontains=search_query)
    
    products = products.order_by('name')
    paginator = Paginator(products, 10)
    page = request.GET.get('page')
    products = paginator.get_page(page)
    return render(request, 'sales_app/product_list.html', {
        'products': products,
        'search_query': search_query
    })

def add_product(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, 'تم إضافة المنتج بنجاح')
            return redirect('sales_app:product_list')
    else:
        form = ProductForm()
    return render(request, 'sales_app/product_form.html', {'form': form})

def edit_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'تم تعديل المنتج بنجاح')
            return redirect('sales_app:product_list')
    else:
        form = ProductForm(instance=product)
    return render(request, 'sales_app/product_form.html', {'form': form})

def delete_product(request, pk):
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=pk)
        product.delete()
        messages.success(request, 'تم حذف المنتج بنجاح')
    return redirect('sales_app:product_list')

@require_http_methods(["GET", "POST"])
def create_invoice(request):
    settings = Settings.objects.first()
    if not settings:
        settings = Settings.objects.create(tax_rate=15)
    
    tax_rate = float(settings.tax_rate) if settings.tax_rate else 0
        
    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        formset = InvoiceItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    invoice = form.save(commit=False)
                    invoice.save()
                    
                    instances = formset.save(commit=False)
                    subtotal = Decimal('0')
                    
                    # تجميع الكميات المطلوبة لكل منتج
                    product_quantities = {}
                    for instance in instances:
                        product_id = instance.product.id
                        if product_id in product_quantities:
                            product_quantities[product_id] += instance.quantity
                        else:
                            product_quantities[product_id] = instance.quantity
                    
                    # التحقق من توفر الكميات الإجمالية
                    for product_id, total_quantity in product_quantities.items():
                        product = Product.objects.select_for_update().get(id=product_id)
                        if product.quantity < total_quantity:
                            messages.error(request, f'الكمية المطلوبة غير متوفرة في المخزون للمنتج {product.name}')
                            raise ValidationError('Insufficient quantity')
                        
                        # تحديث كمية المنتج في المخزون مباشرة
                        product.quantity -= total_quantity
                        product.save()
                    
                    # حفظ عناصر الفاتورة
                    for instance in instances:
                        instance.invoice = invoice
                        instance.save()
                        subtotal += instance.get_subtotal()
                    
                    # تحديث المجموع
                    if invoice.tax_exempt:
                        invoice.total = subtotal
                    else:
                        tax_amount = subtotal * (Decimal(str(tax_rate)) / 100)
                        invoice.total = subtotal + tax_amount
                    
                    invoice.save()
                    
                    # إنشاء دفعة إذا كانت الفاتورة مدفوعة
                    if request.POST.get('payment_status') == 'paid':
                        Payment.objects.create(
                            invoice=invoice,
                            amount=invoice.total,
                            date=timezone.now(),
                            payment_method='cash'
                        )
                        invoice.status = 'paid'
                        invoice.save()
                    
                    messages.success(request, 'تم إنشاء الفاتورة بنجاح')
                    return redirect('sales_app:invoice_detail', pk=invoice.pk)
                    
            except ValidationError:
                # رسالة الخطأ تم إضافتها مسبقاً
                pass
            except Exception as e:
                messages.error(request, 'حدث خطأ أثناء حفظ الفاتورة')
                
    else:
        form = InvoiceForm()
        formset = InvoiceItemFormSet()
    
    return render(request, 'sales_app/invoice_form.html', {
        'form': form,
        'formset': formset,
        'settings': settings,
        'tax_rate': tax_rate
    })

@require_http_methods(["GET", "POST"])
def edit_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    settings = Settings.objects.first()

    if request.method == 'POST':
        form = InvoiceForm(request.POST, instance=invoice)
        formset = InvoiceItemFormSet(request.POST, instance=invoice)

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    # حساب التغييرات الفعلية في النموذج
                    form_changed = form.has_changed()
                    formset_changed = False
                    
                    # قائمة لتتبع التغييرات في الكميات
                    quantity_changes = {}
                    
                    # فحص التغييرات في العناصر الموجودة
                    original_items = {item.id: item for item in invoice.items.all()}
                    
                    for form in formset.forms:
                        if form.instance.pk and not form.cleaned_data.get('DELETE', False):
                            # عنصر موجود مسبقاً
                            original_item = original_items.get(form.instance.pk)
                            if original_item:
                                if 'quantity' in form.changed_data:
                                    formset_changed = True
                                    new_quantity = form.cleaned_data['quantity']
                                    quantity_difference = new_quantity - original_item.quantity
                                    if quantity_difference != 0:
                                        product_id = form.cleaned_data['product'].id
                                        quantity_changes[product_id] = quantity_changes.get(product_id, 0) + quantity_difference
                        
                        elif not form.instance.pk and not form.cleaned_data.get('DELETE', False):
                            # عنصر جديد
                            formset_changed = True
                            product_id = form.cleaned_data['product'].id
                            quantity_changes[product_id] = quantity_changes.get(product_id, 0) + form.cleaned_data['quantity']
                    
                    # معالجة العناصر المحذوفة
                    for form in formset.forms:
                        if form.instance.pk and form.cleaned_data.get('DELETE', False):
                            formset_changed = True
                            product_id = form.instance.product_id
                            quantity_changes[product_id] = quantity_changes.get(product_id, 0) - form.instance.quantity

                    # إذا لم تكن هناك تغييرات، نقوم بإعادة التوجيه مباشرة
                    if not form_changed and not formset_changed:
                        messages.success(request, 'لم يتم إجراء أي تغييرات على الفاتورة.')
                        return redirect('sales_app:invoice_list')

                    # التحقق من توفر الكميات فقط للتغييرات الفعلية
                    for product_id, quantity_change in quantity_changes.items():
                        if quantity_change > 0:
                            product = Product.objects.select_for_update().get(id=product_id)
                            if product.quantity < quantity_change:
                                messages.error(
                                    request,
                                    f'الكمية المطلوبة غير متوفرة في المخزون للمنتج {product.name}. '
                                    f'الكمية المتوفرة: {product.quantity}'
                                )
                                raise ValidationError('Insufficient quantity')

                    # تطبيق التغييرات على المخزون
                    for product_id, quantity_change in quantity_changes.items():
                        if quantity_change != 0:  # تطبيق التغييرات فقط إذا كان هناك تغيير فعلي
                            product = Product.objects.select_for_update().get(id=product_id)
                            product.quantity -= quantity_change
                            product.save()

                    # حفظ الفاتورة والعناصر
                    invoice = form.save()
                    formset.save()

                    messages.success(request, 'تم تحديث الفاتورة بنجاح.')
                    return redirect('sales_app:invoice_list')

            except ValidationError:
                pass
            except Exception as e:
                messages.error(request, f'حدث خطأ أثناء تحديث الفاتورة: {str(e)}')
    else:
        form = InvoiceForm(instance=invoice)
        formset = InvoiceItemFormSet(instance=invoice)

    return render(request, 'sales_app/invoice_form.html', {
        'form': form,
        'formset': formset,
        'invoice': invoice,
        'tax_rate': settings.tax_rate if settings else 0,
    })

@require_http_methods(["POST"])
def delete_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        with transaction.atomic():
            # استرجاع جميع العناصر المرتبطة بالفاتورة
            invoice_items = invoice.items.select_related('product')
            
            # إعادة الكميات إلى المنتجات
            for item in invoice_items:
                product = item.product
                product.quantity += item.quantity
                product.save()
            
            # حذف جميع المدفوعات المرتبطة
            invoice.payments.all().delete()
            
            # حذف الفاتورة
            invoice.delete()
            
            messages.success(request, 'تم حذف الفاتورة بنجاح، وتمت إعادة الكمية إلى المخزون')
    except Exception as e:
        messages.error(request, f'حدث خطأ أثناء حذف الفاتورة: {str(e)}')
    
    return redirect('sales_app:invoice_list')


def add_invoice_items(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    
    if request.method == 'POST':
        form = InvoiceItemForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                item = form.save(commit=False)
                item.invoice = invoice
                
                # Update product quantity
                product = item.product
                if product.quantity >= item.quantity:
                    product.quantity -= item.quantity
                    product.save()
                    item.save()
                    messages.success(request, 'تم إضافة المنتج للفاتورة بنجاح')
                else:
                    messages.error(request, 'الكمية المطلوبة غير متوفرة في المخزون')
                
                if 'add_another' in request.POST:
                    return redirect('add_invoice_items', invoice_id=invoice.id)
                return redirect('invoice_detail', pk=invoice.id)
    else:
        form = InvoiceItemForm()
    
    return render(request, 'sales_app/invoice_items_form.html', {
        'form': form,
        'invoice': invoice
    })

def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    items = invoice.items.all()
    payments = invoice.payments.all().order_by('-date')
    total = invoice.get_total()
    remaining = invoice.get_remaining_amount()
    
    return render(request, 'sales_app/invoice_detail.html', {
        'invoice': invoice,
        'items': items,
        'payments': payments,
        'total': total,
        'remaining': remaining
    })

def add_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            
            # Validate payment amount
            remaining = invoice.get_remaining_amount()
            if payment.amount <= remaining:
                payment.save()
                
                # Update invoice status
                total_paid = invoice.payment_set.aggregate(total=Sum('amount'))['total'] or 0
                if total_paid >= invoice.get_total():
                    invoice.status = 'paid'
                elif total_paid > 0:
                    invoice.status = 'partial'
                invoice.save()
                
                messages.success(request, 'تم تسجيل الدفعة بنجاح')
                return redirect('invoice_detail', pk=invoice.id)
            else:
                messages.error(request, 'المبلغ المدخل أكبر من المبلغ المتبقي للفاتورة')
    else:
        form = PaymentForm(initial={'invoice': invoice})
    
    return render(request, 'sales_app/payment_form.html', {
        'form': form,
        'invoice': invoice
    })

def invoice_list(request):
    invoices = Invoice.objects.all().order_by('-date')
    paginator = Paginator(invoices, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'sales_app/invoice_list.html', {'page_obj': page_obj})

def payment_list(request):
    payments = Payment.objects.all().order_by('-date')
    paginator = Paginator(payments, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'sales_app/payment_list.html', {'page_obj': page_obj})

def receivable_list(request):
    # Get search parameters
    search_query = request.GET.get('search', '')
    payment_method = request.GET.get('payment_method', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    # Start with all receivables
    receivables = Payment.objects.filter(invoice__isnull=True)

    # Apply filters
    if search_query:
        receivables = receivables.filter(id__icontains=search_query)
    
    if payment_method:
        receivables = receivables.filter(payment_method=payment_method)
    
    if date_from:
        receivables = receivables.filter(date__gte=date_from)
    
    if date_to:
        receivables = receivables.filter(date__lte=date_to)

    # Order by date
    receivables = receivables.order_by('-date')

    # Paginate results
    paginator = Paginator(receivables, 10)
    page = request.GET.get('page')
    receivables = paginator.get_page(page)

    context = {
        'receivables': receivables,
        'search_query': search_query,
        'payment_method': payment_method,
        'date_from': date_from,
        'date_to': date_to
    }
    return render(request, 'sales_app/receivable_list.html', context)

def receivable_detail(request, pk):
    receivable = get_object_or_404(Payment, pk=pk, invoice__isnull=True)
    return render(request, 'sales_app/receivable_detail.html', {'receivable': receivable})

def add_receivable(request):
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = None  # This is a standalone payment
            payment.save()
            messages.success(request, 'تم إضافة سند القبض بنجاح')
            return redirect('sales_app:receivable_list')
    else:
        form = PaymentForm(initial={'date': datetime.datetime.now().date()})
    
    return render(request, 'sales_app/receivable_form.html', {
        'form': form,
        'edit_mode': False
    })

def edit_receivable(request, pk):
    receivable = get_object_or_404(Payment, pk=pk, invoice__isnull=True)
    if request.method == 'POST':
        form = PaymentForm(request.POST, instance=receivable)
        if form.is_valid():
            form.save()
            messages.success(request, 'تم تحديث سند القبض بنجاح')
            return redirect('sales_app:receivable_list')
    else:
        form = PaymentForm(instance=receivable)
    
    return render(request, 'sales_app/receivable_form.html', {
        'form': form,
        'edit_mode': True,
        'receivable': receivable
    })

def print_receivable(request, pk):
    receivable = get_object_or_404(Payment, pk=pk, invoice__isnull=True)
    company_settings = Settings.objects.first()
    
    context = {
        'receivable': receivable,
        'company': company_settings,
        'print_mode': True
    }
    return render(request, 'sales_app/receivable_print.html', context)

def dashboard(request):
    # Get statistics for dashboard
    total_customers = Customer.objects.count()
    total_products = Product.objects.count()
    today = timezone.now().date()
    today_invoices = Invoice.objects.filter(date__date=today).count()
    today_revenue = sum(invoice.get_total() for invoice in Invoice.objects.filter(date__date=today))
    
    # Get recent invoices
    recent_invoices = Invoice.objects.order_by('-date')[:5]
    
    # Get low stock products
    low_stock_products = Product.objects.filter(quantity__lt=5).order_by('quantity')
    
    context = {
        'total_customers': total_customers,
        'total_products': total_products,
        'today_invoices': today_invoices,
        'today_revenue': today_revenue,
        'recent_invoices': recent_invoices,
        'low_stock_products': low_stock_products,
    }
    return render(request, 'sales_app/dashboard.html', context)

def settings_view(request):
    settings = Settings.get_settings()
    return render(request, 'sales_app/settings.html', {'settings': settings})

def save_company_settings(request):
    if request.method == 'POST':
        settings = Settings.get_settings()
        settings.company_name = request.POST.get('company_name', '')
        settings.company_address = request.POST.get('company_address', '')
        settings.company_phone = request.POST.get('company_phone', '')
        settings.company_email = request.POST.get('company_email', '')
        settings.tax_number = request.POST.get('tax_number', '')
        settings.save()
        messages.success(request, 'تم حفظ معلومات الشركة بنجاح')
    return redirect('sales_app:settings')

def save_invoice_settings(request):
    if request.method == 'POST':
        settings = Settings.get_settings()
        settings.tax_rate = request.POST.get('tax_rate', 15.0)
        settings.currency = request.POST.get('currency', 'SAR')
        settings.invoice_notes = request.POST.get('invoice_notes', '')
        settings.save()
        messages.success(request, 'تم حفظ إعدادات الفواتير بنجاح')
    return redirect('sales_app:settings')

def save_notification_settings(request):
    if request.method == 'POST':
        settings = Settings.get_settings()
        settings.low_stock_notifications = 'low_stock_notifications' in request.POST
        settings.payment_notifications = 'payment_notifications' in request.POST
        settings.invoice_notifications = 'invoice_notifications' in request.POST
        settings.save()
        messages.success(request, 'تم حفظ إعدادات الإشعارات بنجاح')
    return redirect('sales_app:settings')

@transaction.atomic
def backup_database(request):
    settings = Settings.get_settings()
    if request.method == 'POST':
        # Implement backup logic here
        settings.last_backup_date = timezone.now()
        settings.save()
        messages.success(request, 'تم إنشاء نسخة احتياطية بنجاح')
        return redirect('sales_app:settings')
    return render(request, 'sales_app/backup.html', {
        'settings': settings
    })

def report_list(request):
    # Get date range from request or default to current month
    end_date = timezone.now()
    start_date = end_date.replace(day=1)  # First day of current month
    
    # Sales metrics
    total_sales = Invoice.objects.filter(
        date__range=[start_date, end_date]
    ).aggregate(
        total=Sum(F('items__quantity') * F('items__price'))
    )['total'] or 0
    
    # Product metrics
    top_products = InvoiceItem.objects.filter(
        invoice__date__range=[start_date, end_date]
    ).values(
        'product__name'
    ).annotate(
        total_quantity=Sum('quantity'),
        total_sales=Sum(F('quantity') * F('price'))
    ).order_by('-total_sales')[:5]
    
    # Customer metrics
    top_customers = Invoice.objects.filter(
        date__range=[start_date, end_date]
    ).values(
        'customer__name'
    ).annotate(
        total_purchases=Sum(F('items__quantity') * F('items__price'))
    ).order_by('-total_purchases')[:5]
    
    context = {
        'start_date': start_date,
        'end_date': end_date,
        'total_sales': total_sales,
        'top_products': top_products,
        'top_customers': top_customers,
    }
    
    return render(request, 'sales_app/report_list.html', context)

def company_info(request):
    """عرض وتحديث معلومات الشركة"""
    settings = Settings.objects.first()
    if not settings:
        settings = Settings.objects.create()

    if request.method == 'POST':
        # تحديث معلومات الشركة
        settings.company_name = request.POST.get('company_name')
        settings.company_address = request.POST.get('company_address')
        settings.company_phone = request.POST.get('company_phone')
        settings.company_email = request.POST.get('company_email')
        settings.tax_number = request.POST.get('tax_number')
        settings.save()
        messages.success(request, 'تم تحديث معلومات الشركة بنجاح')
        return redirect('sales_app:company_info')

    return render(request, 'sales_app/settings/company_info.html', {'settings': settings})

def invoice_settings(request):
    """عرض وتحديث إعدادات الفواتير"""
    settings = Settings.objects.first()
    if not settings:
        settings = Settings.objects.create()

    if request.method == 'POST':
        try:
            # تحديث إعدادات الفواتير
            settings.invoice_prefix = request.POST.get('invoice_prefix', '')
            settings.invoice_footer = request.POST.get('invoice_footer', '')
            
            # معالجة نسبة الضريبة
            tax_rate = request.POST.get('tax_rate', '')
            if tax_rate:
                try:
                    settings.tax_rate = Decimal(tax_rate)
                except InvalidOperation:
                    messages.error(request, 'الرجاء إدخال نسبة ضريبة صحيحة')
                    return redirect('sales_app:invoice_settings')
            
            settings.invoice_terms = request.POST.get('invoice_terms', '')
            settings.invoice_notes = request.POST.get('invoice_notes', '')
            settings.save()
            messages.success(request, 'تم تحديث إعدادات الفواتير بنجاح')
        except Exception as e:
            messages.error(request, f'حدث خطأ أثناء حفظ الإعدادات: {str(e)}')
        
        return redirect('sales_app:invoice_settings')

    return render(request, 'sales_app/settings/invoice_settings.html', {'settings': settings})

def notification_settings(request):
    """عرض وتحديث إعدادات الإشعارات"""
    if request.method == 'POST':
        # معالجة تحديث إعدادات الإشعارات
        pass
    return render(request, 'sales_app/settings/notification_settings.html')

def backup_settings(request):
    """عرض وإدارة النسخ الاحتياطي"""
    if request.method == 'POST':
        # معالجة عملية النسخ الاحتياطي
        pass
    return render(request, 'sales_app/settings/backup_settings.html')

def print_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    settings = Settings.objects.first()  # Get company settings
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(f'Invoice #{invoice.id} - Date: {invoice.date} - Total: {invoice.get_total()}')
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")
    
    # Convert QR code to base64
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    qr_code = base64.b64encode(buffer.getvalue()).decode()
    
    context = {
        'invoice': invoice,
        'settings': settings,  # Pass the entire settings object
        'qr_code': qr_code
    }
    
    return render(request, 'sales_app/invoice_print.html', context)

def get_product_price(request, product_id):
    try:
        product = Product.objects.get(pk=product_id)
        return JsonResponse({'price': str(product.selling_price)})
    except Product.DoesNotExist:
        return JsonResponse({'error': 'Product not found'}, status=404)

def sales_report(request):
    # استخراج التواريخ من الفلتر
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # تجهيز الاستعلام الأساسي للفواتير
    invoices = Invoice.objects.all().order_by('-date')

    # تطبيق فلتر التاريخ إذا تم تحديده
    if start_date:
        invoices = invoices.filter(date__gte=start_date)
    if end_date:
        invoices = invoices.filter(date__lte=end_date)

    # تجميع بيانات المبيعات مع تفاصيل المنتجات والكميات
    sales_data = []
    total_quantity = 0
    total_sales = 0
    total_payments = 0

    for invoice in invoices:
        # حساب المبلغ المدفوع للفاتورة
        invoice_payments = sum(payment.amount for payment in invoice.payments.all())
        invoice_total = invoice.get_total()
        payment_status = 'مدفوع' if invoice_payments >= invoice_total else 'غير مدفوع'

        for item in invoice.items.all():
            sales_data.append({
                'invoice_number': invoice.id,
                'date': invoice.date,
                'customer': invoice.customer.name,
                'product': item.product.name,
                'quantity': item.quantity,
                'price': item.price,
                'total': item.quantity * item.price,
                'payment_status': payment_status
            })
            total_quantity += item.quantity
            total_sales += item.quantity * item.price
        
        total_payments += invoice_payments

    # الترقيم
    paginator = Paginator(sales_data, 20)
    page = request.GET.get('page')
    try:
        sales = paginator.page(page)
    except PageNotAnInteger:
        sales = paginator.page(1)
    except EmptyPage:
        sales = paginator.page(paginator.num_pages)

    context = {
        'sales': sales,
        'total_sales': total_sales,
        'total_payments': total_payments,
        'total_due': total_sales - total_payments,
        'total_quantity': total_quantity,
        'start_date': start_date,
        'end_date': end_date,
        'report_date': timezone.now().date(),
    }
    return render(request, 'sales_app/sales_report.html', context)

def reports_dashboard(request):
    # إحصائيات المبيعات
    total_sales = Invoice.objects.aggregate(total=Sum('total_amount'))['total'] or 0
    total_received = Payment.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_pending = total_sales - total_received
    
    # إحصائيات المنتجات
    top_products = InvoiceItem.objects.values('product__name')\
        .annotate(total_quantity=Sum('quantity'))\
        .order_by('-total_quantity')[:5]
    
    context = {
        'total_sales': total_sales,
        'total_received': total_received,
        'total_pending': total_pending,
        'top_products': top_products,
    }
    return render(request, 'sales_app/reports_dashboard.html', context)

def backup_view(request):
    if request.method == 'POST':
        try:
            # إنشاء نسخة احتياطية من قاعدة البيانات
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_dir = os.path.join(settings.MEDIA_ROOT, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            backup_file = os.path.join(backup_dir, f'backup_{timestamp}.json')
            
            management.call_command('dumpdata', output=backup_file)
            
            messages.success(request, 'تم إنشاء النسخة الاحتياطية بنجاح')
        except Exception as e:
            messages.error(request, f'حدث خطأ أثناء إنشاء النسخة الاحتياطية: {str(e)}')
    
    # الحصول على قائمة النسخ الاحتياطية المتوفرة
    backup_dir = os.path.join(settings.MEDIA_ROOT, 'backups')
    backups = []
    if os.path.exists(backup_dir):
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith('.json')],
            reverse=True
        )
    
    return render(request, 'sales_app/backup.html', {'backups': backups})

def notifications_view(request):
    notifications = Notification.objects.all()
    # تحديث حالة الإشعارات إلى مقروءة
    Notification.objects.filter(is_read=False).update(is_read=True)
    return render(request, 'sales_app/notifications.html', {'notifications': notifications})

def mark_notification_read(request, notification_id):
    if request.method == 'POST':
        notification = get_object_or_404(Notification, id=notification_id)
        notification.is_read = True
        notification.save()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)

def update_invoice_status(request, pk):
    if request.method == 'POST':
        invoice = get_object_or_404(Invoice, pk=pk)
        new_status = request.POST.get('status')
        invoice.status = new_status
        invoice.save()
        messages.success(request, 'تم تحديث حالة الفاتورة بنجاح')
        return redirect('sales_app:invoice_list')

def inventory_report(request):
    # الحصول على بيانات المخزون
    inventory = Product.objects.annotate(
        total_value=F('purchase_price') * F('quantity')
    ).values(
        'id',
        'name',
        'quantity',
        'selling_price',
        'total_value',
        'purchase_price'
    )
    
    # حساب إجمالي قيمة المخزون
    total_inventory_value = sum(item['total_value'] for item in inventory)
    
    context = {
        'inventory': inventory,
        'total_inventory_value': total_inventory_value,
        'report_date': timezone.now().strftime("%Y-%m-%d"),
        'settings': Settings.objects.first()
    }
    return render(request, 'sales_app/inventory_report.html', context)

def payment_receipt_report(request):
    payments = Payment.objects.all().order_by('-date')
    customers = Customer.objects.all()
    
    # تطبيق الفلاتر
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    customer_id = request.GET.get('customer')
    
    if start_date:
        payments = payments.filter(date__gte=start_date)
    if end_date:
        payments = payments.filter(date__lte=end_date)
    if customer_id:
        # تعديل الفلتر ليشمل الدفعات المرتبطة بالزبون مباشرة أو من خلال الفاتورة
        payments = payments.filter(
            Q(customer_id=customer_id) | 
            Q(invoice__customer_id=customer_id)
        )
    
    # حساب المجموع
    total_received = payments.aggregate(total=Sum('amount'))['total'] or 0
    
    # الترقيم
    paginator = Paginator(payments, 20)
    page = request.GET.get('page')
    payments = paginator.get_page(page)
    
    context = {
        'payments': payments,
        'customers': customers,
        'total_received': total_received,
    }
    
    return render(request, 'sales_app/payment_receipt_report.html', context)

def print_payment_receipt(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    context = {
        'payment': payment,
        'company_name': 'اسم الشركة',  # يمكن جلبه من الإعدادات
        'company_address': 'فلسطين',
        'company_phone': '123456789',
        'company_mobile': '123456789',
    }
    return render(request, 'sales_app/print_payment_receipt.html', context)

@require_http_methods(["POST"])
def toggle_payment_status(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    total_amount = invoice.get_total()
    total_payments = invoice.payments.aggregate(total=Sum('amount'))['total'] or 0
    
    if total_payments >= total_amount:
        # If fully paid, mark as unpaid by deleting all payments
        invoice.payments.all().delete()
        invoice.status = 'pending'
        invoice.save()
        status = 'unpaid'
        messages.success(request, 'تم تغيير حالة الفاتورة إلى غير مدفوع')
    else:
        # Calculate remaining amount to avoid overpayment
        remaining_amount = total_amount - total_payments
        # Create payment for the remaining amount only
        Payment.objects.create(
            invoice=invoice,
            amount=remaining_amount,
            date=timezone.now(),
            payment_method='cash'
        )
        invoice.status = 'paid'
        invoice.save()
        status = 'paid'
        messages.success(request, 'تم تغيير حالة الفاتورة إلى مدفوع')
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': status})
    return redirect('sales_app:invoice_list')
