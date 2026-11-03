"""Lista de países para o campo "País de origem" no checkout.

Guardada só para a base de dados/estatísticas internas (ver Order.country
em models.py) -- nunca aparece nos bilhetes nem no email de confirmação.
Nomes em inglês por serem os mais reconhecíveis universalmente num
formulário usado por clientes de qualquer nacionalidade.
"""

COUNTRIES = [
    "Portugal", "Spain", "France", "United Kingdom", "Germany", "Italy",
    "Netherlands", "Belgium", "Switzerland", "Ireland", "Austria",
    "Denmark", "Sweden", "Norway", "Finland", "Poland", "Czech Republic",
    "Slovakia", "Hungary", "Romania", "Bulgaria", "Greece", "Croatia",
    "Slovenia", "Serbia", "Bosnia and Herzegovina", "Albania", "Kosovo",
    "North Macedonia", "Montenegro", "Estonia", "Latvia", "Lithuania",
    "Luxembourg", "Malta", "Cyprus", "Iceland", "Ukraine", "Moldova",
    "Belarus", "Russia", "Turkey",
    "Morocco", "Algeria", "Tunisia", "Egypt", "Libya", "Cape Verde",
    "Guinea-Bissau", "Angola", "Mozambique", "São Tomé and Príncipe",
    "Nigeria", "Ghana", "Senegal", "Kenya", "South Africa", "Ethiopia",
    "Ivory Coast", "Cameroon", "Mali", "Tanzania", "Uganda", "Zimbabwe",
    "Namibia", "Botswana", "Zambia", "Rwanda",
    "United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile",
    "Colombia", "Peru", "Uruguay", "Venezuela", "Ecuador", "Bolivia",
    "Paraguay", "Costa Rica", "Panama", "Cuba", "Dominican Republic",
    "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Jamaica",
    "China", "Japan", "South Korea", "India", "Indonesia", "Thailand",
    "Vietnam", "Philippines", "Malaysia", "Singapore", "Israel",
    "United Arab Emirates", "Saudi Arabia", "Qatar", "Pakistan",
    "Bangladesh", "Sri Lanka", "Nepal", "Georgia", "Armenia", "Azerbaijan",
    "Kazakhstan",
    "Australia", "New Zealand",
    "Other",
]
