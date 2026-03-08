import datetime
from django.http import HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail

from carts.models import CartItem
from orders.forms import OrderForm
from orders.models import Order, OrderProduct, Payment
from django.db import transaction



def payments(request):
    return render(request, 'orders/payments.html')


def place_order(request, total=0, quantity=0):

    current_user = request.user
    cart_items = CartItem.objects.filter(user=current_user)

    cart_count = cart_items.count()

    if cart_count <= 0:
        return redirect('store')

    grand_total = 0
    tax = 0

    for cart_item in cart_items:
        total += (cart_item.product.price * cart_item.quantity)
        quantity += cart_item.quantity

    tax = (18 * total) / 100
    grand_total = total + tax

    if request.method == 'POST':

        form = OrderForm(request.POST)

        if form.is_valid():

            data = Order()
            data.user = current_user
            data.first_name = form.cleaned_data['first_name']
            data.last_name = form.cleaned_data['last_name']
            data.phone = form.cleaned_data['phone']
            data.email = form.cleaned_data['email']
            data.address_line_1 = form.cleaned_data['address_line_1']
            data.address_line_2 = form.cleaned_data['address_line_2']
            data.country = form.cleaned_data['country']
            data.state = form.cleaned_data['state']
            data.city = form.cleaned_data['city']
            data.order_note = form.cleaned_data['order_note']
            data.order_total = grand_total
            data.tax = tax
            data.ip = request.META.get('REMOTE_ADDR')

            data.save()

            # Generate Order Number
            current_date = datetime.date.today().strftime("%Y%m%d")
            order_number = current_date + str(data.id)
            data.order_number = order_number
            data.save()

            order = Order.objects.get(
                user=current_user,
                is_ordered=False,
                order_number=order_number
            )

            context = {
                'order': order,
                'cart_items': cart_items,
                'total': total,
                'tax': tax,
                'grand_total': grand_total,
            }

            return render(request, 'orders/payments.html', context)

    else:
        return redirect('checkout')


@login_required
def confirm_order(request):

    if request.method != "POST":
        return redirect('store')

    order_number = request.POST.get('order_number')

    order = get_object_or_404(
        Order,
        order_number=order_number,
        user=request.user,
        is_ordered=False
    )

    # Create Payment (COD)
    payment = Payment.objects.create(
        user=request.user,
        payment_id="COD" + order_number,
        payment_method="Cash On Delivery",
        amount_paid=order.order_total,
        status="Pending"
    )

    order.payment = payment
    order.is_ordered = True
    order.status = "New"
    order.save()

    cart_items = CartItem.objects.filter(user=request.user)

    # DB transaction so either all orderproducts created and stock updated or none
    with transaction.atomic():
        for item in cart_items:

            # --- determine variation object ---
            variation_obj = None
            try:
                # If CartItem.variation is a ManyToMany (common pattern)
                if hasattr(item, 'variation') and hasattr(item.variation, 'all'):
                    qs = item.variation.all()
                    if qs.exists():
                        # choose the first selected variation object
                        variation_obj = qs.first()
                else:
                    # maybe it's a FK or single object
                    if hasattr(item, 'variation') and item.variation:
                        variation_obj = item.variation
            except Exception:
                variation_obj = None

            # --- derive color/size values from variation (if present) ---
            color_val = ''
            size_val = ''
            if variation_obj:
                cat = getattr(variation_obj, 'variation_category', '') or ''
                val = getattr(variation_obj, 'variation_value', '') or ''
                cat_lower = cat.strip().lower()
                if cat_lower in ('color', 'colour'):
                    color_val = val
                elif cat_lower in ('size', 'sizes'):
                    size_val = val
                else:
                    # If your Variation model uses other categories (e.g. 'Material'),
                    # you can add more mapping rules here or store the pair in a text field.
                    pass

            # --- create OrderProduct (variation may be None if not available) ---
            orderproduct = OrderProduct.objects.create(
                order=order,
                payment=payment,
                user=request.user,
                product=item.product,
                variation=variation_obj,   # will work only if OrderProduct.variation allows null (see note)
                color=color_val,
                size=size_val,
                quantity=item.quantity,
                product_price=item.product.price,
                ordered=True,
            )

            # --- safely reduce stock if your Product has stock field ---
            product = item.product
            if hasattr(product, 'stock'):
                new_stock = max(0, product.stock - item.quantity)
                product.stock = new_stock
                product.save()

    # Clear cart
    cart_items.delete()

    # Send Confirmation Email
    subject = 'Thank you for your order!'
    message = f"""
Hello {order.first_name},

Your order has been placed successfully.

Order Number: {order.order_number}
Total Amount: Rs {order.order_total:.2f}

We will deliver your order soon and collect the payment on delivery.

Thanks
EasyKart Team
"""
    send_mail(
        subject,
        message,
        settings.EMAIL_HOST_USER,
        [order.email],
        fail_silently=True
    )

    context = {
        'order': order,
        'grand_total': order.order_total,
    }

    return render(request, 'orders/order_complete.html', context)