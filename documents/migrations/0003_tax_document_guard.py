from django.db import migrations

# Un documento del RCV no se elimina ni se modifica (ADR 0009). La única actualización
# permitida es completar una vez la referencia a su documento original
# (`referenced_document_id` de NULL a un valor) cuando ese original llega después.
SQL = r"""
CREATE OR REPLACE FUNCTION cuadratura_tax_document_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Los documentos tributarios del RCV no pueden eliminarse';
  END IF;
  IF (to_jsonb(NEW) - 'referenced_document_id') IS DISTINCT FROM (to_jsonb(OLD) - 'referenced_document_id')
     OR (OLD.referenced_document_id IS NOT NULL AND NEW.referenced_document_id IS DISTINCT FROM OLD.referenced_document_id) THEN
    RAISE EXCEPTION 'Un documento tributario del RCV es inmutable; solo puede completarse su referencia pendiente';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER tax_document_guard BEFORE UPDATE OR DELETE ON documents_taxdocument
  FOR EACH ROW EXECUTE FUNCTION cuadratura_tax_document_guard();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS tax_document_guard ON documents_taxdocument;
DROP FUNCTION IF EXISTS cuadratura_tax_document_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("documents", "0002_tax_document")]
    operations = [migrations.RunSQL(SQL, REVERSE_SQL)]
