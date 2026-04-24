"""经济系统 - 金币、背包、商城"""


class EconomyManager:
    """管理金币、背包物品和商城。"""

    def __init__(self, state: dict, items: list):
        self._state = state
        self._items = items

    @property
    def coins(self) -> int:
        return int(self._state.get("coins", 0))

    @coins.setter
    def coins(self, val: int):
        self._state["coins"] = max(0, int(val))

    def add_coins(self, val: int) -> None:
        self.coins = self.coins + val

    @property
    def items(self) -> list:
        return self._items

    def get_item(self, index: int) -> dict | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def feed_item(self, index: int) -> tuple[bool, str]:
        """使用背包物品，返回 (成功?, 回复文本)。"""
        item = self.get_item(index)
        if item is None:
            return False, "没有这个物品。"
        if item.get("qty", 0) <= 0:
            return False, f"{item['name']}已经用完了。"
        item["qty"] = item.get("qty", 1) - 1
        return True, item.get("reply", f"吃了{item['name']}！")

    def buy_item(self, index: int, shop_items: list) -> tuple[bool, str]:
        """从商城购买物品，返回 (成功?, 回复文本)。"""
        if index < 0 or index >= len(shop_items):
            return False, "没有这个商品。"
        shop_item = shop_items[index]
        price = shop_item.get("price", 0)
        if self.coins < price:
            return False, "金币不够啦。"
        self.coins = self.coins - price
        # 添加到背包
        found = False
        for inv_item in self._items:
            if inv_item.get("name") == shop_item.get("name"):
                inv_item["qty"] = inv_item.get("qty", 0) + 1
                found = True
                break
        if not found:
            self._items.append({
                "name": shop_item["name"],
                "qty": 1,
                "price": shop_item.get("price", 0),
                "affinity": shop_item.get("affinity", 1),
                "reply": shop_item.get("reply", ""),
            })
        return True, f"购买了{shop_item['name']}！"
