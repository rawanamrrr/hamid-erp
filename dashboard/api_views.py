from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from accounts.permissions import require_permission
from django.db.models import Sum, F, Q, Count, Max, Min, FloatField, ExpressionWrapper
from django.utils import timezone
from datetime import timedelta
from products.models import Product, StockTransaction
from sales.models import Order, OrderItem
from crm.models import Customer
from decimal import Decimal

@login_required
@require_permission('dashboard', 'view')
def product_health_api(request):
    products = Product.objects.all().prefetch_related('images', 'sizes')
    total_products = products.count()
    if total_products == 0:
        return JsonResponse({'total_products': 0})
        
    complete_count = 0
    needs_review_count = 0
    incomplete_count = 0
    total_score = 0
    
    worst_products = []
    
    for p in products:
        score = 0
        missing = []
        
        # REQUIRED (weight 2)
        if p.name: score += 2
        else: missing.append("الاسم")
        
        if p.sku: score += 2
        else: missing.append("SKU")
        
        if p.price_retail > 0: score += 2
        else: missing.append("سعر البيع")
        
        if p.cost_price > 0: score += 2
        else: missing.append("سعر التكلفة")
        
        if p.category_id: score += 2
        else: missing.append("القسم")
        
        if p.supplier_id: score += 2
        else: missing.append("المورد")
        
        if p.stock_quantity > 0: score += 2
        else: missing.append("الكمية")
        
        # OPTIONAL (weight 1)
        if p.barcode: score += 1
        else: missing.append("الباركود")
        
        # prefetch_related is used to avoid N+1
        if list(p.images.all()): score += 1
        else: missing.append("الصور")
        
        if list(p.sizes.all()): score += 1
        else: missing.append("المقاسات")
        
        if p.material or p.pattern or p.color: score += 1
        else: missing.append("الخامة/النقشة/اللون")
        
        if p.unit_measure: score += 1
        else: missing.append("الوحدة")
        
        pct = (score / 19.0) * 100
        total_score += pct
        
        if pct >= 90:
            complete_count += 1
        elif pct >= 60:
            needs_review_count += 1
        else:
            incomplete_count += 1
            
        if pct < 90:
            worst_products.append({
                'id': p.id,
                'name': p.name,
                'sku': p.sku,
                'score': round(pct, 1),
                'missing_fields': missing
            })
            
    worst_products.sort(key=lambda x: x['score'])
    worst_products = worst_products[:20]
    
    avg_score = total_score / total_products
    
    return JsonResponse({
        'total_products': total_products,
        'complete_count': complete_count,
        'needs_review_count': needs_review_count,
        'incomplete_count': incomplete_count,
        'overall_health_score': round(avg_score, 1),
        'incomplete_products': worst_products
    })

@login_required
@require_permission('dashboard', 'view')
def smart_suggestions_api(request):
    suggestions = []
    
    thirty_days_ago = timezone.now() - timedelta(days=30)
    
    products = Product.objects.filter(is_active=True).annotate(
        recent_sales=Sum('transactions__quantity', filter=Q(transactions__transaction_type='OUT', transactions__created_at__gte=thirty_days_ago))
    )
    
    # Calculate top 20% sales velocity threshold
    sales_list = [p.recent_sales for p in products if p.recent_sales]
    sales_list.sort(reverse=True)
    top_20_idx = max(1, int(len(sales_list) * 0.2)) - 1
    top_velocity_threshold = sales_list[top_20_idx] if sales_list else 999999
    
    for p in products:
        qty = float(p.stock_quantity or 0)
        alert = float(p.low_stock_threshold or 0)
        cost = float(p.cost_price or 0)
        price = float(p.price_retail or 0)
        recent_sales = float(p.recent_sales or 0)
        
        # A. نفد من المخزون / على وشك النفاد — raw materials only. A cafe menu item
        # (drink/food) is prepared on demand and never carries a real stock count, so it
        # would otherwise always show as "out of stock" here.
        if p.is_raw_material:
            if qty == 0:
                suggestions.append({
                    'type': 'out_of_stock',
                    'priority': 'high',
                    'product_id': p.id,
                    'product_name': p.name,
                    'sku': p.sku,
                    'message_ar': 'نفد من المخزون - أضف مخزون فوراً',
                    'action_label': 'إضافة مخزون',
                    'action_url': reverse('product_update', args=[p.id]),
                    'icon': 'fa-triangle-exclamation'
                })
            elif qty <= alert:
                suggestions.append({
                    'type': 'low_stock',
                    'priority': 'medium',
                    'product_id': p.id,
                    'product_name': p.name,
                    'sku': p.sku,
                    'message_ar': f'الكمية الحالية ({qty}) أقل من حد التنبيه ({alert})',
                    'action_label': 'أعد الطلب',
                    'action_url': reverse('product_update', args=[p.id]),
                    'icon': 'fa-boxes-packing'
                })
            
        # B. بطيئة الحركة
        if recent_sales == 0 and qty > 10:
            suggestions.append({
                'type': 'slow_mover',
                'priority': 'medium',
                'product_id': p.id,
                'product_name': p.name,
                'sku': p.sku,
                'message_ar': 'منتج بطيء - فكر في تخفيض السعر أو عمل عرض خاص',
                'action_label': 'تعديل السعر',
                'action_url': reverse('product_update', args=[p.id]),
                'icon': 'fa-hourglass-half'
            })
            
        # C. أكثر مبيعاً لكن مخزون منخفض
        if recent_sales > 0 and recent_sales >= top_velocity_threshold and qty < 10:
            suggestions.append({
                'type': 'hot_low_stock',
                'priority': 'high',
                'product_id': p.id,
                'product_name': p.name,
                'sku': p.sku,
                'message_ar': 'منتج رائج ومخزونه منخفض - أعد الطلب بأولوية عالية 🔥',
                'action_label': 'إضافة مخزون',
                'action_url': reverse('product_update', args=[p.id]),
                'icon': 'fa-fire'
            })
            
        # D. هامش ربح منخفض
        if price > 0:
            margin = (price - cost) / price
            if margin < 0.10:
                suggestions.append({
                    'type': 'low_margin',
                    'priority': 'low',
                    'product_id': p.id,
                    'product_name': p.name,
                    'sku': p.sku,
                    'message_ar': f'هامش الربح {margin*100:.1f}% فقط - راجع السعر أو التكلفة',
                    'action_label': 'تعديل السعر',
                    'action_url': reverse('product_update', args=[p.id]),
                    'icon': 'fa-percent'
                })
                
        # E. بيانات ناقصة تؤثر على المبيعات
        if recent_sales > 0:
            if not p.barcode:
                suggestions.append({
                    'type': 'missing_barcode',
                    'priority': 'low',
                    'product_id': p.id,
                    'product_name': p.name,
                    'sku': p.sku,
                    'message_ar': 'أضف باركود لتسريع نقطة البيع للمنتجات الرائجة',
                    'action_label': 'تعديل المنتج',
                    'action_url': reverse('product_update', args=[p.id]),
                    'icon': 'fa-barcode'
                })

    priority_map = {'high': 0, 'medium': 1, 'low': 2}
    suggestions.sort(key=lambda x: priority_map.get(x['priority'], 99))
    
    return JsonResponse({'suggestions': suggestions})

@login_required
@require_permission('dashboard', 'view')
def price_suggestions_api(request):
    six_months_ago = timezone.now() - timedelta(days=180)
    
    products = Product.objects.filter(is_active=True, price_retail__gt=0).annotate(
        sales_vol=Sum('transactions__quantity', filter=Q(transactions__transaction_type='OUT'))
    )
    
    opportunities = []
    warnings = []
    total_impact = 0
    
    for p in products:
        cost = float(p.cost_price or 0)
        retail = float(p.price_retail or 0)
        wholesale = float(p.price_wholesale or 0)
        sales_vol = float(p.sales_vol or 0)
        
        # Target 20% margin
        margin = (retail - cost) / retail if retail > 0 else 0
        target_margin = 0.20
        
        if cost > 0 and margin < 0.15:
            # Suggest price to reach 20% margin: price = cost / (1 - 0.20)
            suggested_price = cost / (1 - target_margin)
            
            # Impact if we increase price, assuming same volume (e.g. monthly)
            # Roughly estimating monthly sales = total / 3 if 3 months, but let's just use recent volume
            monthly_vol = sales_vol / 3 if sales_vol > 0 else 0 # Just a rough estimate
            impact = (suggested_price - retail) * monthly_vol if monthly_vol > 0 else 0
            
            total_impact += impact
            
            opportunities.append({
                'product_id': p.id,
                'product_name': p.name,
                'current_price': round(retail, 2),
                'suggested_price': round(suggested_price, 2),
                'current_margin_pct': round(margin * 100, 1),
                'projected_margin_pct': round(target_margin * 100, 1),
                'reason_ar': 'هامش ربح أقل من 15%، نقترح رفعه للوصول إلى 20%',
                'estimated_monthly_impact': round(impact, 2),
                'action_type': 'increase'
            })
            
        # Warnings
        if wholesale > 0 and wholesale >= retail:
            warnings.append({
                'product_id': p.id,
                'product_name': p.name,
                'current_price': round(retail, 2),
                'suggested_price': round(wholesale * 1.1, 2), # 10% above wholesale
                'current_margin_pct': round(margin * 100, 1),
                'projected_margin_pct': 0,
                'reason_ar': 'خطأ: سعر الجملة أعلى من أو يساوي القطاعي',
                'estimated_monthly_impact': 0,
                'action_type': 'fix_wholesale'
            })
        elif wholesale > 0 and (retail - wholesale) / retail < 0.10:
             warnings.append({
                'product_id': p.id,
                'product_name': p.name,
                'current_price': round(retail, 2),
                'suggested_price': round(wholesale * 1.15, 2),
                'current_margin_pct': round(margin * 100, 1),
                'projected_margin_pct': 0,
                'reason_ar': 'فجوة صغيرة جداً بين الجملة والقطاعي',
                'estimated_monthly_impact': 0,
                'action_type': 'adjust_gap'
            })
            
        # Staleness
        if p.updated_at and p.updated_at < six_months_ago:
            warnings.append({
                'product_id': p.id,
                'product_name': p.name,
                'current_price': round(retail, 2),
                'suggested_price': round(retail * 1.05, 2),
                'current_margin_pct': round(margin * 100, 1),
                'projected_margin_pct': 0,
                'reason_ar': 'لم يُحدَّث السعر منذ 6 أشهر - راجع التكلفة الحالية',
                'estimated_monthly_impact': 0,
                'action_type': 'stale_price'
            })

    # Sort opportunities by estimated impact
    opportunities.sort(key=lambda x: x['estimated_monthly_impact'], reverse=True)
    opportunities = opportunities[:15]
    
    warnings = warnings[:15]
    
    return JsonResponse({
        'total_impact': round(sum(o['estimated_monthly_impact'] for o in opportunities), 2),
        'opportunities': opportunities,
        'warnings': warnings
    })

@login_required
@require_permission('dashboard', 'view')
def crm_suggestions_api(request):
    suggestions = []
    
    thirty_days_ago = timezone.now() - timedelta(days=30)
    ninety_days_ago = timezone.now() - timedelta(days=90)
    
    # 1. Churning customers
    customers = Customer.objects.annotate(
        total_orders=Count('order'),
        last_order_date=Max('order__created_at'),
        total_spent=Sum('order__total_amount')
    )
    
    churn_count = 0
    vip_count = 0
    
    # Calculate top 10% threshold
    spending_list = [c.total_spent for c in customers if c.total_spent]
    spending_list.sort(reverse=True)
    top_10_idx = max(1, int(len(spending_list) * 0.1)) - 1
    vip_threshold = spending_list[top_10_idx] if spending_list else 999999
    
    for c in customers:
        if c.total_orders >= 3 and c.last_order_date and c.last_order_date < thirty_days_ago:
            churn_count += 1
            days_absent = (timezone.now() - c.last_order_date).days
            suggestions.append({
                'type': 'churning',
                'priority': 'high',
                'message_ar': f'عميل قديم ({c.first_name}) لم يشترِ منذ {days_absent} يوم - تواصل معه',
                'action_label': 'عرض العميل',
                'related_customer_id': c.id,
                'icon': 'fa-user-clock'
            })
            
        if c.total_spent and c.total_spent >= vip_threshold:
            vip_count += 1
            if c.last_order_date and c.last_order_date > thirty_days_ago:
                suggestions.append({
                    'type': 'vip',
                    'priority': 'medium',
                    'message_ar': f'عميل VIP ({c.first_name}) - قدم له عرضاً خاصاً للاحتفاظ به',
                    'action_label': 'عرض العميل',
                    'related_customer_id': c.id,
                    'icon': 'fa-crown'
                })

    # Avg Invoice Value
    recent_orders = Order.objects.filter(created_at__gte=thirty_days_ago)
    previous_orders = Order.objects.filter(created_at__gte=thirty_days_ago - timedelta(days=30), created_at__lt=thirty_days_ago)
    
    avg_recent = recent_orders.aggregate(avg=Sum('total_amount') / Count('id'))['avg'] or 0
    avg_prev = previous_orders.aggregate(avg=Sum('total_amount') / Count('id'))['avg'] or 0
    
    if avg_prev > 0 and (avg_prev - avg_recent) / avg_prev > 0.15:
        suggestions.append({
            'type': 'avg_invoice_drop',
            'priority': 'high',
            'message_ar': f'متوسط الفاتورة انخفض بنسبة >15% عن الشهر الماضي - فكر في اقتراح منتجات إضافية عند البيع',
            'action_label': 'تحليل المبيعات',
            'icon': 'fa-arrow-trend-down'
        })
        
    # Basket Analysis (Simple)
    # Just a placeholder for actual basket analysis which is slow in SQL
    
    # Peak Times
    from django.db.models.functions import ExtractHour, ExtractWeekDay
    peak_times = Order.objects.filter(created_at__gte=ninety_days_ago)\
        .annotate(hour=ExtractHour('created_at'), weekday=ExtractWeekDay('created_at'))\
        .values('weekday', 'hour').annotate(count=Count('id')).order_by('-count')
        
    if peak_times:
        best = peak_times[0]
        days = {1: 'الأحد', 2: 'الاثنين', 3: 'الثلاثاء', 4: 'الأربعاء', 5: 'الخميس', 6: 'الجمعة', 7: 'السبت'}
        day_name = days.get(best['weekday'], 'يوم')
        suggestions.append({
            'type': 'peak_time',
            'priority': 'low',
            'message_ar': f'ذروة المبيعات: يوم {day_name} الساعة {best["hour"]}:00 - تأكد من توفر المخزون',
            'action_label': 'تجهيز المخزون',
            'icon': 'fa-chart-line'
        })

    heatmap = list(peak_times)

    return JsonResponse({
        'churn_count': churn_count,
        'vip_count': vip_count,
        'avg_invoice': round(avg_recent, 2),
        'suggestions': suggestions[:15],
        'heatmap': heatmap
    })
