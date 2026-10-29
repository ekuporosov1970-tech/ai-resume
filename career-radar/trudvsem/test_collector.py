import unittest
from collector import (
    GROUP_QUERIES, HEADERS, clean, normalize, remote_status, salary_text,
    safe_vacancy_url, sheet_row, unique,
)

class CollectorTests(unittest.TestCase):
    def fixture(self, duty="Предусмотрена удалённая работа"):
        return {"vacancy":{
          "id":"abc123","job-name":"Копирайтер","company":{"name":"Тест Компания"},
          "salary_min":35000,"salary_max":45000,"region":{"name":"Москва"},
          "requirement":{"experience":"0"},"schedule":"Гибкий график",
          "duty":duty,"vac_url":"https://trudvsem.ru/vacancy/card/123/abc123",
        }}
    def test_required_groups(self):
        self.assertEqual(len(GROUP_QUERIES), 14)
        self.assertIn("контент-аналитик", GROUP_QUERIES["Менеджер-аналитик"])
    def test_positive_remote(self):
        self.assertEqual(remote_status(self.fixture()["vacancy"]), "Есть указание")
    def test_missing_remote_is_not_asserted(self):
        self.assertEqual(remote_status(self.fixture("Работа по графику")["vacancy"]), "Не подтверждено")
    def test_negated_remote(self):
        self.assertEqual(remote_status(self.fixture("Удалённая работа не предусмотрена")["vacancy"]), "Противоречие / офис")
    def test_reject_foreign_url(self):
        self.assertEqual(safe_vacancy_url({"vac_url":"https://example.com/" }),"")
    def test_normalize(self):
        out=normalize(self.fixture(),"Копирайтер","29.10.2026")
        self.assertEqual(out["remote"],"Есть указание")
        self.assertEqual(out["experience"],"0 лет")
        self.assertEqual(out["salary"],"35000–45000 ₽")
        self.assertEqual(len(sheet_row(out)), len(HEADERS))
    def test_dedup(self):
        v=normalize(self.fixture(),"Копирайтер","29.10.2026")
        self.assertEqual(len(unique([v,v])), 1)
    def test_formula_safe(self):
        self.assertEqual(clean("=WEBSERVICE(123)"),"'=WEBSERVICE(123)")
    def test_zero_upper_salary(self):
        self.assertEqual(salary_text({"salary_min": 50000, "salary_max": 0}), "от 50000 ₽")
    def test_irrelevant_title_rejected(self):
        item = self.fixture()
        item["vacancy"]["job-name"] = "маркетолог"
        self.assertIsNone(normalize(item,"Копирайтер","29.10.2026"))
    def test_wrong_remote_negation(self):
        item = self.fixture("Удаленная работа не предусмотрена")
        self.assertEqual(remote_status(item["vacancy"]), "Противоречие / офис")
    def test_null_safe(self):
        self.assertEqual(salary_text({}),"Не указана")

if __name__ == "__main__":
    unittest.main(verbosity=2)