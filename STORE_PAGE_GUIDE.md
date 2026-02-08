# Store Page Implementation Guide

Quick reference for building the product store page in the Player App.

## Overview

The store page allows players to:
1. **Browse** available products for their plan
2. **View** product details (subjects, price, description)
3. **Purchase** products → creates transaction → awaits admin approval
4. **Exclude** already-purchased and pending-purchase items

---

## API Endpoints

### 1. GET `/api/v1/catalog/`

**Purpose**: Fetch available products for the player's plan

**Note**: Include trailing slash to avoid 307 redirect

**Headers Required**:
```
Authorization: Bearer {access_token}
Content-Type: application/json
```

**Response**: `200 OK`
```json
{
  "products": [
    {
      "product_grant_id": "GRNT-00001",
      "bundle_name": "أساسيات اللغة العربية",
      "price": 29.99,
      "subjects": [
        {
          "subject_id": "SUBJ-00015",
          "alias_title": "مقدمة العربية",
          "notes": "يشمل الحروف والأصوات والقواعس الأساسية"
        },
        {
          "subject_id": "SUBJ-00016",
          "alias_title": "الكتابة والإملاء",
          "notes": null
        }
      ]
    },
    {
      "product_grant_id": "GRNT-00002",
      "bundle_name": "الرياضيات المتقدمة",
      "price": 39.99,
      "subjects": [
        {
          "subject_id": "SUBJ-00050",
          "alias_title": "الجبر والمعادلات",
          "notes": "مستوى متقدم مع تطبيقات عملية"
        }
      ]
    }
  ]
}
```

**What the API Handles**:
- ✅ Filters out products player already purchased (has access to ALL subjects)
- ✅ Filters out products with pending transactions
- ✅ Returns empty array if no products available
- ✅ Returns empty array if player has no plan (but HTTP 200)

**Errors**:
- `401 Unauthorized`: Not authenticated or token expired
- `503 Service Unavailable`: Redis/database issue

**Caching**:
- <100ms on subsequent requests (Redis cache per plan)
- Cache invalidated when Product Grant changes in Frappe

---

### 2. POST `/api/v1/purchase/`

**Purpose**: Submit purchase request for a product

**Note**: Include trailing slash to avoid 307 redirect

**Headers Required**:
```
Authorization: Bearer {access_token}
Content-Type: application/json
```

**Request Body**:
```json
{
  "product_grant_id": "GRNT-00001"
}
```

**Response**: `201 Created`
```json
{
  "transaction_id": "TRANS-00042",
  "product_grant_id": "GRNT-00001",
  "status": "Pending Approval",
  "created_at": "2026-02-08T10:30:00Z"
}
```

**What Happens**:
- ✅ Creates Subscription Transaction in Frappe (status = "Pending Approval")
- ✅ Adds to player's Redis pending set
- ✅ Product immediately hidden from catalog
- ✅ Admin notified via email

**Errors**:
- `400 Bad Request`: Player has no plan
- `404 Not Found`: Product grant not found
- `409 Conflict`: Duplicate pending purchase for this product
- `503 Service Unavailable`: Redis unavailable

**After Purchase**:
- Product remains hidden until admin approves/rejects
- On approval: subscriptions created, access granted
- On rejection: product reappears in catalog

---

## Data Models

### CatalogProduct
```typescript
interface CatalogProduct {
  product_grant_id: string;      // e.g., "GRNT-00001"
  bundle_name: string;            // e.g., "أساسيات اللغة العربية"
  price: number;                  // e.g., 29.99
  subjects: CatalogSubject[];     // Array of subjects in this product
}
```

### CatalogSubject
```typescript
interface CatalogSubject {
  subject_id: string;             // e.g., "SUBJ-00015"
  alias_title: string | null;     // Product-specific name
  notes: string | null;           // Product-specific description
}
```

### PurchaseResponse
```typescript
interface PurchaseResponse {
  transaction_id: string;         // e.g., "TRANS-00042"
  product_grant_id: string;       // e.g., "GRNT-00001"
  status: string;                 // "Pending Approval"
  created_at: string;             // ISO 8601 timestamp
}
```

---

## Store Page Workflow

### Initial Load
```
1. User opens Store tab
2. Frontend: GET /api/v1/catalog
3. Display loading spinner
4. Response arrives (<100ms with cache)
5. Display products in grid/list
```

### User Clicks Product
```
1. Show product detail modal/page with:
   - Bundle name (large)
   - Price (prominent)
   - List of subjects included
   - Subject descriptions (alias_title + notes)
   - [Purchase] button
```

### User Submits Purchase
```
1. User clicks [Purchase] button
2. Show confirmation dialog: "Purchase for SAR {price}?"
3. On confirm:
   - Disable button (loading state)
   - POST /api/v1/purchase {product_grant_id}
4. On success:
   - Show success message: "Purchase submitted for approval"
   - Remove product from visible list
   - Auto-refresh catalog after 2 sec (GET /api/v1/catalog)
5. On error:
   - Show error message
   - Re-enable button for retry
   - Handle specific errors:
     - 409: "You already have a pending purchase for this product"
     - 400: "Unable to purchase (no plan assigned)"
     - 503: "Service temporarily unavailable. Please try again."
```

### Pending Products
```
- Once purchased, product hidden from catalog
- Status: "Pending Approval"
- Admin reviews in Frappe Desk
- Player sees nothing until approved/rejected
- On approval: subjects become accessible in Progress view
```

---

## React Component Structure

### Suggested Components

```
<StorePage>
  ├── <LoadingSpinner /> (initial load)
  ├── <ProductGrid>
  │   └── <ProductCard> (for each product)
  │       ├── Product image (optional)
  │       ├── Bundle name
  │       ├── Price (SAR)
  │       ├── Subject count badge
  │       └── [View Details] button
  ├── <ProductDetailModal>
  │   ├── Bundle name (large)
  │   ├── Price (SAR)
  │   ├── <SubjectsList>
  │   │   └── <SubjectItem> (alias_title + notes)
  │   ├── [Close] button
  │   └── [Purchase] button
  └── <ErrorAlert /> (if needed)
```

### State Management Example

```typescript
const [products, setProducts] = useState<CatalogProduct[]>([]);
const [loading, setLoading] = useState(true);
const [selectedProduct, setSelectedProduct] = useState<CatalogProduct | null>(null);
const [purchasing, setPurchasing] = useState(false);
const [error, setError] = useState<string | null>(null);

// Fetch catalog on mount
useEffect(() => {
  fetchCatalog();
}, []);

const fetchCatalog = async () => {
  try {
    setLoading(true);
    const response = await fetch('/api/v1/catalog', {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await response.json();
    setProducts(data.products);
  } catch (err) {
    setError('Failed to load products');
  } finally {
    setLoading(false);
  }
};

const handlePurchase = async (productGrantId: string) => {
  try {
    setPurchasing(true);
    const response = await fetch('/api/v1/purchase', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ product_grant_id: productGrantId })
    });

    if (!response.ok) {
      throw new Error(`Purchase failed: ${response.statusText}`);
    }

    // Success
    setError(null);
    setSelectedProduct(null);

    // Refresh catalog
    setTimeout(() => fetchCatalog(), 1000);

  } catch (err) {
    setError(err.message);
  } finally {
    setPurchasing(false);
  }
};
```

---

## Error Handling

### Common Errors & UI Messages

| Error | HTTP | User Message | Action |
|-------|------|--------------|--------|
| Token expired | 401 | "Session expired. Please log in again." | Redirect to login |
| No plan | 400 | "Unable to purchase (no plan assigned)" | Contact support link |
| Duplicate purchase | 409 | "You already have a pending purchase for this product" | Show pending status |
| Network error | - | "Connection lost. Please try again." | Retry button |
| Service down | 503 | "Service temporarily unavailable. Try again in a moment." | Auto-retry in 30s |
| Product not found | 404 | "Product not available" | Refresh catalog |

---

## Example Flow (Full Code)

```typescript
// StorePage.tsx
import { useEffect, useState } from 'react';

interface Product {
  product_grant_id: string;
  bundle_name: string;
  price: number;
  subjects: Array<{
    subject_id: string;
    alias_title: string | null;
    notes: string | null;
  }>;
}

export function StorePage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [purchasing, setPurchasing] = useState(false);

  const token = localStorage.getItem('access_token');

  useEffect(() => {
    fetchProducts();
  }, []);

  const fetchProducts = async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(`/api/v1/catalog/`, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        if (response.status === 401) {
          // Token expired, redirect to login
          window.location.href = '/login';
          return;
        }
        throw new Error(`Failed to load products: ${response.statusText}`);
      }

      const data = await response.json();
      setProducts(data.products);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  const handlePurchase = async (productGrantId: string) => {
    try {
      setPurchasing(true);
      setError(null);

      const response = await fetch('/api/v1/purchase/', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ product_grant_id: productGrantId })
      });

      if (!response.ok) {
        const errorData = await response.json();

        if (response.status === 409) {
          throw new Error('You already have a pending purchase for this product');
        } else if (response.status === 400) {
          throw new Error('Unable to purchase (no plan assigned)');
        } else if (response.status === 503) {
          throw new Error('Service temporarily unavailable. Please try again.');
        }

        throw new Error(errorData.detail || 'Purchase failed');
      }

      // Success
      setSelectedProduct(null);
      alert('Purchase submitted! Admin will review your request.');

      // Refresh catalog after 1 second
      setTimeout(() => fetchProducts(), 1000);

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Purchase failed');
    } finally {
      setPurchasing(false);
    }
  };

  if (loading) return <div>Loading products...</div>;

  return (
    <div className="store-page">
      <h1>Product Store</h1>

      {error && <div className="error-alert">{error}</div>}

      {products.length === 0 ? (
        <p>No products available for your plan.</p>
      ) : (
        <div className="product-grid">
          {products.map((product) => (
            <div key={product.product_grant_id} className="product-card">
              <h3>{product.bundle_name}</h3>
              <p className="price">SAR {product.price.toFixed(2)}</p>
              <p className="subjects-count">
                {product.subjects.length} subject{product.subjects.length !== 1 ? 's' : ''}
              </p>
              <button onClick={() => setSelectedProduct(product)}>
                View Details
              </button>
            </div>
          ))}
        </div>
      )}

      {selectedProduct && (
        <div className="modal">
          <div className="modal-content">
            <h2>{selectedProduct.bundle_name}</h2>
            <p className="price">SAR {selectedProduct.price.toFixed(2)}</p>

            <h3>Included Subjects:</h3>
            <ul>
              {selectedProduct.subjects.map((subject) => (
                <li key={subject.subject_id}>
                  <strong>{subject.alias_title || subject.subject_id}</strong>
                  {subject.notes && <p>{subject.notes}</p>}
                </li>
              ))}
            </ul>

            <div className="modal-actions">
              <button onClick={() => setSelectedProduct(null)}>Close</button>
              <button
                onClick={() => handlePurchase(selectedProduct.product_grant_id)}
                disabled={purchasing}
              >
                {purchasing ? 'Processing...' : 'Purchase'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

---

## Testing the Store API

### Development (via Vite Proxy)

When running your React app with `npm run dev`, use the Vite proxy:

```bash
# Health check
curl http://localhost:5173/api/v1/health/live

# Fetch catalog
TOKEN="your-access-token"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:5173/api/v1/catalog/

# Submit purchase
TOKEN="your-access-token"
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"product_grant_id": "GRNT-00001"}' \
  http://localhost:5173/api/v1/purchase/
```

### Production (Direct to x.conanacademy.com)

For production builds or direct API testing:

```bash
# Fetch catalog
TOKEN="your-access-token"
curl -H "Authorization: Bearer $TOKEN" \
  https://x.conanacademy.com/api/v1/catalog/

# Submit purchase
TOKEN="your-access-token"
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"product_grant_id": "GRNT-00001"}' \
  https://x.conanacademy.com/api/v1/purchase/
```

---

## Fixing 307 Redirect Issues

If you're getting `307 Temporary Redirect`, the issue is **missing trailing slash**:

### ✅ Correct
```typescript
fetch('/api/v1/catalog/', { ... })
fetch('/api/v1/purchase/', { ... })
```

### ❌ Wrong (causes 307)
```typescript
fetch('/api/v1/catalog', { ... })   // 307 redirect
fetch('/api/v1/purchase', { ... })  // 307 redirect
```

**Other things to check:**

1. **Include headers**: Always include `Content-Type: application/json`
2. **Vite proxy running**: Make sure `npm run dev` is running
3. **Token format**: `Authorization: Bearer {token}` (with space)
4. **Base URL**: During dev, requests go through Vite proxy (localhost:5173)

**Remember: Always include the trailing slash!**
```bash
# ✅ Correct (with trailing slash)
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:5173/api/v1/catalog/

# ❌ Wrong (307 redirect without trailing slash)
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:5173/api/v1/catalog
```

---

## Key Points to Remember

1. **Vite Proxy**: In development, all `/api/*` requests proxy to `https://x.conanacademy.com`
2. **Caching**: First request takes ~200ms, subsequent requests <100ms
3. **Filtering**: API already filters purchased & pending items
4. **Single-Purchase**: Cannot submit 2 purchases for same product
5. **Pending Items**: Hidden until admin approves/rejects
6. **No Plan**: Player with no plan gets empty catalog (HTTP 200)
7. **Token Required**: All requests need `Authorization: Bearer {token}` header
8. **Error Handling**: Always check response status and handle errors

