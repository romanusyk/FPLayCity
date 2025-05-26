import math


class Loss:

    def score(self, labels: list[float], predictions: list[float]) -> float:
        pass


class LogLoss(Loss):

    def score(self, labels: list[float], predictions: list[float]) -> float:
        epsilon = 1e-15
        loss = 0.
        for label, prediction in zip(labels, predictions):
            prediction = min(1 - epsilon, prediction)
            prediction = max(epsilon, prediction)
            loss -= label * math.log(prediction) + (1 - label) * math.log(1 - prediction)
        return loss


class AvgDiffLoss(Loss):

    def score(self, labels: list[float], predictions: list[float]) -> float:
        pos_sum = 0.
        pos_count = 0
        neg_sum = 0.
        neg_count = 0
        for label, prediction in zip(labels, predictions):
            if label == 1:
                pos_sum += prediction
                pos_count += 1
            else:
                neg_sum += prediction
                neg_count += 1
        if pos_count == 0 or neg_count == 0:
            return 0.
        return 1. - (pos_sum / pos_count - neg_sum / neg_count)
