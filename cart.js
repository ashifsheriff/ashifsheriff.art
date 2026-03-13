// Cart logic — localStorage-backed, injected UI

const CART_KEY = 'ashif_cart';

const Cart = {
  get() {
    try {
      return JSON.parse(localStorage.getItem(CART_KEY)) || [];
    } catch {
      return [];
    }
  },

  save(items) {
    localStorage.setItem(CART_KEY, JSON.stringify(items));
  },

  add(productId) {
    const items = this.get();
    if (!items.includes(productId)) {
      items.push(productId);
      this.save(items);
    }
    this.updateBadge();
    this.renderDrawer();
  },

  remove(productId) {
    const items = this.get().filter(id => id !== productId);
    this.save(items);
    this.updateBadge();
    this.renderDrawer();
  },

  count() {
    return this.get().length;
  },

  total() {
    const items = this.get();
    return items.reduce((sum, id) => {
      const product = (typeof PRODUCTS !== 'undefined' ? PRODUCTS : []).find(p => p.id === id);
      return sum + (product ? product.price : 0);
    }, 0);
  },

  updateBadge() {
    const count = this.count();
    document.querySelectorAll('.cart-count').forEach(el => {
      el.textContent = count > 0 ? count : '';
      el.style.display = count > 0 ? 'inline' : 'none';
    });
    document.querySelectorAll('.cart-icon-btn').forEach(el => {
      el.setAttribute('aria-label', `Bag (${count} item${count !== 1 ? 's' : ''})`);
    });
  },

  renderDrawer() {
    const drawer = document.getElementById('cart-drawer');
    if (!drawer) return;

    const items = this.get();
    const products = typeof PRODUCTS !== 'undefined' ? PRODUCTS : [];
    const cartItems = items.map(id => products.find(p => p.id === id)).filter(Boolean);

    const listHTML = cartItems.length === 0
      ? '<p class="cart-empty">Your bag is empty.</p>'
      : cartItems.map(p => `
          <div class="cart-item">
            <img src="./images/${p.images[0]}" alt="${p.name}" />
            <div class="cart-item-info">
              <span class="cart-item-name">${p.name}</span>
              <span class="cart-item-price">₹${p.price.toLocaleString()}</span>
            </div>
            <button class="cart-remove" onclick="Cart.remove('${p.id}')" aria-label="Remove ${p.name}">×</button>
          </div>
        `).join('');

    const total = this.total();
    drawer.querySelector('.cart-items').innerHTML = listHTML;
    drawer.querySelector('.cart-total-amount').textContent = `₹${total.toLocaleString()}`;
    drawer.querySelector('.cart-checkout-btn').disabled = items.length === 0;
  },

  openDrawer() {
    const drawer = document.getElementById('cart-drawer');
    const overlay = document.getElementById('cart-overlay');
    if (drawer) drawer.classList.add('open');
    if (overlay) overlay.classList.add('visible');
    document.body.style.overflow = 'hidden';
  },

  closeDrawer() {
    const drawer = document.getElementById('cart-drawer');
    const overlay = document.getElementById('cart-overlay');
    if (drawer) drawer.classList.remove('open');
    if (overlay) overlay.classList.remove('visible');
    document.body.style.overflow = '';
  }
};

// Inject cart drawer HTML and initialise on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  // Inject drawer
  const drawerHTML = `
    <div id="cart-overlay" onclick="Cart.closeDrawer()"></div>
    <div id="cart-drawer" role="dialog" aria-label="Shopping bag">
      <div class="cart-drawer-header">
        <h2>Bag</h2>
        <button class="cart-close-btn" onclick="Cart.closeDrawer()" aria-label="Close bag">×</button>
      </div>
      <div class="cart-items"></div>
      <div class="cart-drawer-footer">
        <div class="cart-total">
          <span>Total</span>
          <span class="cart-total-amount">₹0</span>
        </div>
        <button class="cart-checkout-btn button" disabled>Checkout — coming soon</button>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML('beforeend', drawerHTML);

  // Wire up cart icon buttons
  document.querySelectorAll('.cart-icon-btn').forEach(btn => {
    btn.addEventListener('click', () => Cart.openDrawer());
  });

  // Initial state
  Cart.updateBadge();
  Cart.renderDrawer();
});
