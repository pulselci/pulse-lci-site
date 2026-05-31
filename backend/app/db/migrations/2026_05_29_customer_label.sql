-- Add customer_label to businesses table
-- Allows each business to specify what they call their customers
-- (e.g. "patients" for dental, "customers" for auto repair, "clients" for law firms)

ALTER TABLE public.businesses
  ADD COLUMN IF NOT EXISTS customer_label TEXT NOT NULL DEFAULT 'customers';

-- Backfill existing dental/medical businesses (optional — update manually as needed)
-- UPDATE public.businesses SET customer_label = 'patients' WHERE name ILIKE '%dental%' OR name ILIKE '%orthodontic%';
