# ----------------------------------------------------------------------------
# Copyright (C) 2021 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
#
"""Module contains Train Test label Drift check."""

from typing import Dict, Hashable, Callable, Tuple, List, Union

from plotly.subplots import make_subplots

from deepchecks import CheckResult, TrainTestBaseCheck, ConditionResult
from deepchecks.utils.distribution.plot import drift_score_bar_traces, get_density
from deepchecks.utils.plot import colors
from deepchecks.vision.base.vision_dataset import TaskType
from deepchecks.vision import VisionDataset
import numpy as np
from collections import Counter
import plotly.graph_objs as go


__all__ = ['TrainTestLabelDrift']

class TrainTestLabelDrift(TrainTestBaseCheck):
    """
    Calculate label drift between train dataset and test dataset, using statistical measures.

    Check calculates a drift score for the label in test dataset, by comparing its distribution to the train
    dataset.
    For numerical columns, we use the Earth Movers Distance.
    See https://www.lexjansen.com/wuss/2017/47_Final_Paper_PDF.pdf
    For categorical columns, we use the Population Stability Index (PSI).
    See https://en.wikipedia.org/wiki/Wasserstein_metric.


    Args:
        max_num_categories (int):
            Only for categorical columns. Max number of allowed categories. If there are more,
            they are binned into an "Other" category. If max_num_categories=None, there is no limit. This limit applies
            for both drift calculation and for distribution plots.
    """

    def __init__(
            self,
            max_num_categories: int = 10
    ):
        super().__init__()
        self.max_num_categories = max_num_categories

    def run(self, train_dataset, test_dataset, model=None) -> CheckResult:
        """Run check.

        Args:
            train_dataset (VisionDataset): The training dataset object.
            test_dataset (VisionDataset): The test dataset object.
            model: not used in this check.

        Returns:
            CheckResult:
                value: dictionary of column name to drift score.
                display: distribution graph for each column, comparing the train and test distributions.

        Raises:
            DeepchecksValueError: If the object is not a Dataset or DataFrame instance
        """
        return self._calc_drift(train_dataset, test_dataset)

    def _calc_drift(
            self,
            train_dataset: VisionDataset,
            test_dataset: VisionDataset,
    ) -> CheckResult:
        """
        Calculate drift for all columns.

        Args:
            train_dataset (VisionDataset): The training dataset object. Must contain a label.
            test_dataset (VisionDataset): The test dataset object. Must contain a label.

        Returns:
            CheckResult:
                value: drift score.
                display: label distribution graph, comparing the train and test distributions.
        """
        train_dataset = VisionDataset.validate_dataset(train_dataset)
        test_dataset = VisionDataset.validate_dataset(test_dataset)
        train_dataset.validate_label()
        test_dataset.validate_label()

        task_type = train_dataset.label_type
        if task_type == TaskType.CLASSIFICATION:
            train_label_distribution = train_dataset.get_samples_per_class()
            test_label_distribution = test_dataset.get_samples_per_class()

            drift_score, method, display = calc_drift_and_plot(
                train_distribution=train_label_distribution,
                test_distribution=test_label_distribution,
                plot_title='Class',
                column_type='categorical',
                max_num_categories=self.max_num_categories
            )

        else:
            raise NotImplementedError('Currently not implemented') #TODO

        headnote = """<span>
            The Drift score is a measure for the difference between two distributions, in this check - the test
            and train distributions.<br> The check shows the drift score and distributions for the label.
        </span>"""

        displays = [headnote, display]
        values_dict = {'Drift score': drift_score, 'Method': method}

        return CheckResult(value=values_dict, display=displays, header='Train Test Label Drift')

    def add_condition_drift_score_not_greater_than(self, max_allowed_psi_score: float = 0.2,
                                                   max_allowed_earth_movers_score: float = 0.1):
        """
        Add condition - require drift score to not be more than a certain threshold.

        The industry standard for PSI limit is above 0.2.
        Earth movers does not have a common industry standard.

        Args:
            max_allowed_psi_score: the max threshold for the PSI score
            max_allowed_earth_movers_score: the max threshold for the Earth Mover's Distance score

        Returns:
            ConditionResult: False if any column has passed the max threshold, True otherwise
        """

        def condition(result: Dict) -> ConditionResult:
            drift_score = result['Drift score']
            method = result['Method']
            has_failed = (drift_score > max_allowed_psi_score and method == 'PSI') or \
                         (drift_score > max_allowed_earth_movers_score and method == "Earth Mover's Distance")

            if method == 'PSI' and has_failed:
                return_str = f'Found label PSI above threshold: {drift_score:.2f}'
                return ConditionResult(False, return_str)
            elif method == "Earth Mover's Distance" and has_failed:
                return_str = f'Label\'s Earth Mover\'s Distance above threshold: {drift_score:.2f}'
                return ConditionResult(False, return_str)

            return ConditionResult(True)

        return self.add_condition(f'PSI <= {max_allowed_psi_score} and Earth Mover\'s Distance <= '
                                  f'{max_allowed_earth_movers_score} for label drift',
                                  condition)

PSI_MIN_PERCENTAGE = 0.01

def psi(expected_percents: np.ndarray, actual_percents: np.ndarray):
    """
    Calculate the PSI (Population Stability Index).

    See https://www.lexjansen.com/wuss/2017/47_Final_Paper_PDF.pdf

    Args:
        expected_percents: array of percentages of each value in the expected distribution.
        actual_percents: array of percentages of each value in the actual distribution.

    Returns:
        psi: The PSI score

    """
    psi_value = 0
    for i in range(len(expected_percents)):
        # In order for the value not to diverge, we cap our min percentage value
        e_perc = max(expected_percents[i], PSI_MIN_PERCENTAGE)
        a_perc = max(actual_percents[i], PSI_MIN_PERCENTAGE)
        value = (e_perc - a_perc) * np.log(e_perc / a_perc)
        psi_value += value

    return psi_value


# def earth_movers_distance(dist1: Union[np.ndarray, pd.Series], dist2: Union[np.ndarray, pd.Series]):
#     """
#     Calculate the Earth Movers Distance (Wasserstein distance).
#
#     See https://en.wikipedia.org/wiki/Wasserstein_metric
#
#     Function is for numerical data only.
#
#     Args:
#         dist1: array of numberical values.
#         dist2: array of numberical values to compare dist1 to.
#
#     Returns:
#         the Wasserstein distance between the two distributions.
#
#     """
#     unique1 = np.unique(dist1)
#     unique2 = np.unique(dist2)
#
#     sample_space = list(set(unique1).union(set(unique2)))
#
#     val_max = max(sample_space)
#     val_min = min(sample_space)
#
#     if val_max == val_min:
#         return 0
#
#     dist1 = (dist1 - val_min) / (val_max - val_min)
#     dist2 = (dist2 - val_min) / (val_max - val_min)
#
#     return wasserstein_distance(dist1, dist2)

# def preprocess_2_cat_cols_to_same_bins(dist1: np.ndarray, dist2: np.ndarray, max_num_categories
#                                        ) -> Tuple[np.ndarray, np.ndarray, List]:
#     """
#     Preprocess distributions to the same bins in order to be able to be calculated by PSI.
#
#     Function returns the value counts for each distribution and the categories list. If there are more than
#     max_num_categories, it encodes rare categories into an "OTHER" category. This is done according to the values of
#     dist1, which is treated as the "expected" distribution.
#
#     Function is for categorical data only.
#     Args:
#         dist1: list of values from the first distribution, treated as the expected distribution
#         dist2: list of values from the second distribution, treated as the actual distribution
#         max_num_categories: max number of allowed categories. If there are more, they are binned into an "Other"
#         category. If max_num_categories=None, there is no limit.
#
#     Returns:
#         dist1_percents: array of percentages of each value in the first distribution.
#         dist2_percents: array of percentages of each value in the second distribution.
#         categories_list: list of all categories that the percentages represent.
#
#     """
#     all_categories = list(set(dist1).union(set(dist2)))
#
#     if max_num_categories is not None and len(all_categories) > max_num_categories:
#         dist1_counter = dict(Counter(dist1).most_common(max_num_categories))
#         dist1_counter['Other rare categories'] = len(dist1) - sum(dist1_counter.values())
#         categories_list = list(dist1_counter.keys())
#
#         dist2_counter = Counter(dist2)
#         dist2_counter = {k: dist2_counter[k] for k in categories_list}
#         dist2_counter['Other rare categories'] = len(dist2) - sum(dist2_counter.values())
#
#     else:
#         dist1_counter = Counter(dist1)
#         dist2_counter = Counter(dist2)
#         categories_list = all_categories
#
#     dist1_percents = np.array([dist1_counter[k] for k in categories_list]) / len(dist1)
#     dist2_percents = np.array([dist2_counter[k] for k in categories_list]) / len(dist2)
#
#     return dist1_percents, dist2_percents, categories_list



def calc_drift_and_plot(train_distribution: Counter, test_distribution: Counter, plot_title: Hashable,
                        column_type: str, max_num_categories: int = 10) -> Tuple[float, str, Callable]:
    """
    Calculate drift score per column.

    Args:
        train_distribution: column from train dataset
        test_distribution: same column from test dataset
        plot_title: title of plot
        column_type: type of column (either "numerical" or "categorical")
        max_num_categories: Max number of allowed categories. If there are more, they are binned into an "Other"
                            category.

    Returns:
        score: drift score of the difference between the two columns' distributions (Earth movers distance for
        numerical, PSI for categorical)
        display: graph comparing the two distributions (density for numerical, stack bar for categorical)
    """
    # train_dist = train_distribution.dropna().values.reshape(-1)
    # test_dist = test_distribution.dropna().values.reshape(-1)

    if column_type == 'numerical':
        pass
        # scorer_name = "Earth Mover's Distance"
        #
        # train_dist = train_dist.astype('float')
        # test_dist = test_dist.astype('float')
        #
        # score = earth_movers_distance(dist1=train_dist, dist2=test_dist)
        # bar_stop = max(0.4, score + 0.1)
        #
        # score_bar = drift_score_bar_traces(score)
        #
        # traces, xaxis_layout, yaxis_layout = feature_distribution_traces(train_dist, test_dist)

    elif column_type == 'categorical':
        scorer_name = 'PSI'
        # expected_percents, actual_percents, _ = \
        #     preprocess_2_cat_cols_to_same_bins(dist1=train_dist, dist2=test_dist, max_num_categories=max_num_categories)

        categories_list = list(set(train_distribution.keys()).union(set(test_distribution.keys())))

        expected_percents = np.array([train_distribution[k] for k in categories_list]) / np.sum(list(train_distribution.values()))
        actual_percents = np.array([test_distribution[k] for k in categories_list]) / np.sum(list(test_distribution.values()))

        score = psi(expected_percents=expected_percents, actual_percents=actual_percents)
        bar_stop = min(max(0.4, score + 0.1), 1)

        score_bar = drift_score_bar_traces(score)

        traces, xaxis_layout, yaxis_layout = feature_distribution_traces(expected_percents, actual_percents, categories_list,
                                                                         is_categorical=True, max_num_categories=max_num_categories)

    fig = make_subplots(rows=2, cols=1, vertical_spacing=0.4, shared_yaxes=False, shared_xaxes=False,
                        row_heights=[0.1, 0.9],
                        subplot_titles=['Drift Score - ' + scorer_name, plot_title])

    fig.add_traces(score_bar, rows=[1] * len(score_bar), cols=[1] * len(score_bar))
    fig.add_traces(traces, rows=[2] * len(traces), cols=[1] * len(traces))

    shared_layout = go.Layout(
        xaxis=dict(
            showgrid=False,
            gridcolor='black',
            linecolor='black',
            range=[0, bar_stop],
            dtick=0.05
        ),
        yaxis=dict(
            showgrid=False,
            showline=False,
            showticklabels=False,
            zeroline=False,
        ),
        xaxis2=xaxis_layout,
        yaxis2=yaxis_layout,
        legend=dict(
            title='Dataset',
            yanchor='top',
            y=0.6),
        width=700,
        height=400
    )

    fig.update_layout(shared_layout)

    return score, scorer_name, fig

def feature_distribution_traces(expected_percents: np.array,
                                actual_percents: np.array,
                                categories_list: list,
                                is_categorical: bool = False,
                                max_num_categories: int = 10) -> [List[Union[go.Bar, go.Scatter]], Dict, Dict]:
    """Create traces for comparison between train and test column.

    Args:
        train_column (): Train data used to trace distribution.
        test_column (): Test data used to trace distribution.
        is_categorical (bool): State if column is categorical (default: False).
        max_num_categories (int): Maximum number of categories to show in plot (default: 10).

    Returns:
        List[Union[go.Bar, go.Scatter]]: list of plotly traces.
        Dict: layout of x axis
        Dict: layout of y axis
    """
    if is_categorical:
        # expected_percents, actual_percents, categories_list = \
        #     preprocess_2_cat_cols_to_same_bins(dist1=train_column, dist2=test_column,
        #                                        max_num_categories=max_num_categories)



        train_bar = go.Bar(
            x=categories_list,
            y=expected_percents,
            marker=dict(
                color=colors['Train'],
            ),
            name='Train Dataset',
        )

        test_bar = go.Bar(
            x=categories_list,
            y=actual_percents,
            marker=dict(
                color=colors['Test'],
            ),
            name='Test Dataset',
        )

        traces = [train_bar, test_bar]

        xaxis_layout = dict(type='category')
        yaxis_layout = dict(fixedrange=True,
                            range=(0, 1),
                            title='Percentage')

    else:
        pass
        # x_range = (min(train_column.min(), test_column.min()), max(train_column.max(), test_column.max()))
        # xs = np.linspace(x_range[0], x_range[1], 40)
        #
        # traces = [go.Scatter(x=xs, y=get_density(train_column, xs), fill='tozeroy', name='Train Dataset',
        #                      line_color=colors['Train']),
        #           go.Scatter(x=xs, y=get_density(test_column, xs), fill='tozeroy', name='Test Dataset',
        #                      line_color=colors['Test'])]
        #
        # xaxis_layout = dict(fixedrange=True,
        #                     range=x_range,
        #                     title='Distribution')
        # yaxis_layout = dict(title='Probability Density')

    return traces, xaxis_layout, yaxis_layout
