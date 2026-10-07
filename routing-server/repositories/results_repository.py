class ResultsRepository:
    def __init__(self, database):
        self.collection = database["inference_results"]

    async def save_result(self, result):
        await self.collection.insert_one(result)

    async def get_results_by_model(self, department_name: str, project_name: str, model_name: str):
        return await self.collection.find({
            "department_name": department_name,
            "project_name": project_name,
            "model_name": model_name
        }).to_list(length=100)
