"""v30 request reserve with shared source-navigation tool in work phase."""
from budget_policy_v30 import BudgetPolicy


class NavigationBudgetPolicy(BudgetPolicy):
    @staticmethod
    def allowed_tools(decision, offered_names):
        if decision.phase != 'work':
            return BudgetPolicy.allowed_tools(decision, offered_names)
        names = {'scoped_editor', 'scoped_tests', 'scoped_symbols', 'finish'} & set(offered_names)
        if decision.public_verification != 'passed_current':
            names.discard('finish')
        if not names:
            raise RuntimeError('Required budget-phase tool unavailable')
        return names
